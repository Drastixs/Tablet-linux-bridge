package com.tabletbridge

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity(), BridgeService.Listener {

    private lateinit var screen: ImageView
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        screen = findViewById(R.id.screen)
        status = findViewById(R.id.status)
        requestPermissionsThenStart()
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        BridgeService.listener = this
        onConnection(BridgeService.isConnected)
    }

    override fun onPause() {
        if (BridgeService.listener === this) BridgeService.listener = null
        super.onPause()
    }

    private fun requestPermissionsThenStart() {
        val needed = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            needed += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        if (needed.isEmpty()) startBridge() else ActivityCompat.requestPermissions(this, needed.toTypedArray(), 1)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, R.string.mic_permission_needed, Toast.LENGTH_LONG).show()
        }
        startBridge()
    }

    private fun startBridge() {
        val intent = Intent(this, BridgeService::class.java)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun hideSystemBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.let {
                it.hide(WindowInsets.Type.systemBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION)
        }
    }

    // ------------------------------------------------------------ BridgeService.Listener

    override fun onConnection(connected: Boolean) {
        runOnUiThread {
            if (connected) {
                status.text = getString(R.string.connected)
                status.visibility = View.GONE
            } else {
                screen.setImageDrawable(null)
                status.text = getString(R.string.waiting)
                status.visibility = View.VISIBLE
            }
        }
    }

    override fun onFrame(bitmap: Bitmap) {
        runOnUiThread {
            status.visibility = View.GONE
            screen.setImageBitmap(bitmap)
        }
    }
}
