package com.tabletbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * Foreground service that listens on localhost (reached via `adb forward`) and:
 *  - streams the tablet microphone to the laptop,
 *  - plays PCM received from the laptop on the tablet speaker,
 *  - hands received JPEG frames to the UI.
 */
class BridgeService : Service() {

    interface Listener {
        fun onConnection(connected: Boolean)
        fun onFrame(bitmap: Bitmap)
    }

    companion object {
        private const val TAG = "BridgeService"
        private const val CHANNEL_ID = "bridge"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "com.tabletbridge.STOP"

        @Volatile var listener: Listener? = null
        @Volatile var isConnected = false
            private set
    }

    private val running = AtomicBoolean(false)
    private var serverSocket: ServerSocket? = null
    private var client: Socket? = null

    private var micThread: Thread? = null
    private val micEnabled = AtomicBoolean(false)
    private var audioTrack: AudioTrack? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startInForeground()
        if (running.compareAndSet(false, true)) {
            thread(name = "bridge-accept") { acceptLoop() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running.set(false)
        closeClient()
        runCatching { serverSocket?.close() }
        super.onDestroy()
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Tablet Bridge", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, BridgeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notification: Notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("Tablet Bridge")
            .setContentText(if (isConnected) "Connected to laptop" else "Waiting for laptop over USB")
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun setConnected(connected: Boolean) {
        isConnected = connected
        listener?.onConnection(connected)
        startInForeground()
    }

    // ---------------------------------------------------------------- networking

    private fun acceptLoop() {
        try {
            // Only listen on loopback: the laptop reaches us via `adb forward tcp:PORT tcp:PORT`.
            val server = ServerSocket(Protocol.PORT, 1, InetAddress.getLoopbackAddress())
            server.reuseAddress = true
            serverSocket = server
            Log.i(TAG, "listening on ${server.localSocketAddress}")
            while (running.get()) {
                val socket = try { server.accept() } catch (e: Exception) { break }
                socket.tcpNoDelay = true
                closeClient()
                client = socket
                handleClient(socket)
            }
        } catch (e: Exception) {
            Log.e(TAG, "accept loop failed", e)
        } finally {
            runCatching { serverSocket?.close() }
            running.set(false)
        }
    }

    private fun handleClient(socket: Socket) {
        val input = DataInputStream(socket.getInputStream().buffered(256 * 1024))
        val output = BufferedOutputStream(socket.getOutputStream(), 64 * 1024)
        try {
            val hello = Protocol.read(input)
            if (hello.type != Protocol.HELLO) throw IllegalStateException("expected HELLO")
            Protocol.write(output, Protocol.HELLO, JSONObject().put("version", Protocol.VERSION).toString().toByteArray())
            Log.i(TAG, "laptop connected: ${String(hello.payload)}")
            setConnected(true)

            while (running.get() && !socket.isClosed) {
                val msg = Protocol.read(input)
                when (msg.type) {
                    Protocol.SPK_PCM -> playSpeaker(msg.payload)
                    Protocol.FRAME_JPEG -> showFrame(msg.payload)
                    Protocol.CONTROL -> handleControl(JSONObject(String(msg.payload)), output)
                    Protocol.HELLO -> Unit
                    else -> Log.w(TAG, "unknown message type ${msg.type}")
                }
            }
        } catch (e: Exception) {
            Log.i(TAG, "laptop disconnected: ${e.message}")
        } finally {
            stopMic()
            releaseSpeaker()
            runCatching { socket.close() }
            setConnected(false)
        }
    }

    private fun closeClient() {
        stopMic()
        releaseSpeaker()
        runCatching { client?.close() }
        client = null
    }

    private fun handleControl(json: JSONObject, output: BufferedOutputStream) {
        if (json.has("mic")) {
            if (json.getBoolean("mic")) startMic(output) else stopMic()
        }
    }

    // ---------------------------------------------------------------- microphone

    private fun startMic(output: BufferedOutputStream) {
        if (micEnabled.getAndSet(true)) return
        micThread = thread(name = "bridge-mic") {
            val minBuf = AudioRecord.getMinBufferSize(
                Protocol.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
            )
            val recorder = try {
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    Protocol.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(minBuf, Protocol.SAMPLE_RATE / 5 * 2)
                )
            } catch (e: SecurityException) {
                Log.e(TAG, "no RECORD_AUDIO permission", e)
                micEnabled.set(false)
                return@thread
            }
            if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord failed to initialise")
                recorder.release()
                micEnabled.set(false)
                return@thread
            }
            // 20 ms chunks: 48000 * 0.02 * 2 bytes
            val chunk = ByteArray(Protocol.SAMPLE_RATE / 50 * 2)
            try {
                recorder.startRecording()
                while (micEnabled.get()) {
                    val n = recorder.read(chunk, 0, chunk.size)
                    if (n <= 0) continue
                    Protocol.write(output, Protocol.MIC_PCM, chunk, n)
                }
            } catch (e: Exception) {
                Log.i(TAG, "mic stream ended: ${e.message}")
            } finally {
                runCatching { recorder.stop() }
                recorder.release()
                micEnabled.set(false)
            }
        }
    }

    private fun stopMic() {
        micEnabled.set(false)
        micThread?.let { runCatching { it.join(1000) } }
        micThread = null
    }

    // ---------------------------------------------------------------- speaker

    private fun speaker(): AudioTrack {
        audioTrack?.let { return it }
        val minBuf = AudioTrack.getMinBufferSize(
            Protocol.SAMPLE_RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT
        )
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(Protocol.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                    .build()
            )
            // ~200 ms of buffer keeps playback smooth over adb without adding much latency.
            .setBufferSizeInBytes(maxOf(minBuf, Protocol.SAMPLE_RATE / 5 * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track.play()
        audioTrack = track
        return track
    }

    private fun playSpeaker(pcm: ByteArray) {
        val track = speaker()
        var off = 0
        while (off < pcm.size) {
            val n = track.write(pcm, off, pcm.size - off)
            if (n < 0) {
                Log.w(TAG, "AudioTrack write error $n")
                return
            }
            off += n
        }
    }

    private fun releaseSpeaker() {
        audioTrack?.let {
            runCatching { it.pause(); it.flush(); it.release() }
        }
        audioTrack = null
    }

    // ---------------------------------------------------------------- display

    private val bitmapOptions = BitmapFactory.Options().apply { inPreferredConfig = Bitmap.Config.RGB_565 }

    private fun showFrame(jpeg: ByteArray) {
        val l = listener ?: return // nobody is looking; skip decoding
        val bmp = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, bitmapOptions) ?: return
        l.onFrame(bmp)
    }
}
