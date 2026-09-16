package com.tabletbridge

import java.io.DataInputStream
import java.io.EOFException
import java.io.OutputStream

/**
 * Wire format (both directions): 1 byte type, 4 byte big-endian payload length, payload.
 * Kept identical to linux/tabletbridge/protocol.py.
 */
object Protocol {
    const val PORT = 27183
    const val VERSION = 1

    const val HELLO: Int = 0x01        // JSON {"version":1}
    const val MIC_PCM: Int = 0x10      // tablet -> laptop, s16le 48k mono
    const val SPK_PCM: Int = 0x20      // laptop -> tablet, s16le 48k stereo
    const val FRAME_JPEG: Int = 0x30   // laptop -> tablet
    const val CONTROL: Int = 0x40      // laptop -> tablet, JSON {"mic":bool}

    const val SAMPLE_RATE = 48000
    const val MAX_PAYLOAD = 16 * 1024 * 1024

    class Message(val type: Int, val payload: ByteArray)

    fun read(input: DataInputStream): Message {
        val type = input.read()
        if (type < 0) throw EOFException()
        val len = input.readInt()
        if (len < 0 || len > MAX_PAYLOAD) throw IllegalStateException("bad frame length $len")
        val payload = ByteArray(len)
        input.readFully(payload)
        return Message(type, payload)
    }

    fun write(out: OutputStream, type: Int, payload: ByteArray, len: Int = payload.size) {
        val header = byteArrayOf(
            type.toByte(),
            (len ushr 24).toByte(), (len ushr 16).toByte(), (len ushr 8).toByte(), len.toByte()
        )
        synchronized(out) {
            out.write(header)
            out.write(payload, 0, len)
            out.flush()
        }
    }
}
