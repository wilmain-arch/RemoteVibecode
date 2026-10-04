package ru.wilmain.codexphone

import android.graphics.Bitmap
import android.content.Context
import android.net.Uri
import android.util.Log
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.core.app.ActivityScenario
import kotlinx.coroutines.runBlocking
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HeldCertificate
import okhttp3.tls.HandshakeCertificates
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.TimeUnit
import java.security.MessageDigest
import kotlin.coroutines.intrinsics.COROUTINE_SUSPENDED
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

/** Regression contracts: isolated fixture only, no real account or personal files. */
class AuditRegressionUiTest {
    @get:Rule val compose = createComposeRule()

    @Test fun authoritativeHistoryRetainsOlderPartOfSameTurn() {
        val old = listOf(ChatLine("assistant", "older", "turn", id="a"),
            ChatLine("assistant", "removed", "turn", id="b"),
            ChatLine("assistant", "latest", "turn", id="c"))
        val page = listOf(old.last())
        val snapshot = org.json.JSONObject().put("hasMore", true)
            .put("existingTurnIds", org.json.JSONArray(listOf("turn")))
            .put("existingMessageIds", org.json.JSONArray(listOf("a", "c")))
        assertEquals(listOf("a", "c"), mergeHistorySnapshot(old, page, snapshot).map { it.id })
    }

    @Test fun checksumFailurePreservesExistingDestination() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeTrue(context.packageName.endsWith("taskfixture"))
        val destination = File(context.cacheDir, "synthetic-audit-existing.txt")
        val server = MockWebServer()
        val cert = HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").build()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory(), false)
        val trust = HandshakeCertificates.Builder().addTrustedCertificate(cert.certificate).build()
        val client = OkHttpClient.Builder().sslSocketFactory(trust.sslSocketFactory(), trust.trustManager).build()
        server.enqueue(MockResponse().setBody("CORRUPT-SYNTHETIC").setHeader("X-Content-SHA256", "0".repeat(64)))
        server.start()
        destination.writeText("ORIGINAL-SYNTHETIC")
        try {
            val method = Class.forName("ru.wilmain.codexphone.MainActivityKt").declaredMethods.single { it.name == "downloadFile" }
            method.isAccessible = true
            var failure: Throwable? = null
            try {
                runBlocking {
                    val progress: suspend (Long, Long) -> Unit = { _, _ -> }
                    suspendCoroutine<Unit> { continuation ->
                        val returned = method.invoke(null, context, client, server.url("/").toString().trimEnd('/'), "synthetic-audit-token",
                            RemoteFile("fixture", "synthetic.txt", 16), Uri.fromFile(destination),
                            AtomicReference<Call?>(), progress, continuation)
                        if (returned !== COROUTINE_SUSPENDED) continuation.resume(Unit)
                    }
                }
            } catch (problem: Throwable) { failure = problem }
            assertNotNull("Checksum must fail", failure)
            assertTrue("Expected checksum failure, got: $failure", failure.toString().contains("Контрольная сумма"))
            assertEquals("ORIGINAL-SYNTHETIC", destination.readText())
            Log.i("RV-AUDIT", "Checksum failure safely preserves existing destination")
        } finally { destination.delete(); server.shutdown() }
    }

    @Test fun reopeningImageFetchesReplacement() {
        val count = AtomicInteger()
        val visible = mutableStateOf(true)
        fun png(color: Int): ByteArray = ByteArrayOutputStream().use { stream ->
            val bitmap = Bitmap.createBitmap(64, 64, Bitmap.Config.ARGB_8888)
            bitmap.eraseColor(color); bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream); bitmap.recycle()
            stream.toByteArray()
        }
        var bytes = png(android.graphics.Color.RED)
        val endpoint = "/api/workspace/image?threadId=synthetic-audit&path=${UUID.randomUUID()}.png"
        compose.setContent { MaterialTheme { if (visible.value) RemoteImage(endpoint, "synthetic", {
            count.incrementAndGet(); bytes
        }, Modifier.size(100.dp)) else Text("Hidden") } }
        compose.waitUntil(5000) { count.get() == 1 }
        compose.waitForIdle()
        compose.runOnIdle { visible.value = false }
        compose.waitForIdle()
        compose.runOnIdle { bytes = png(android.graphics.Color.BLUE); visible.value = true }
        compose.waitForIdle()
        compose.waitUntil(5000) { count.get() == 2 }
        assertEquals(2, count.get())
        Log.i("RV-AUDIT", "Reopening image fetches current bytes")
    }

    @Test fun removedServerHistoryDisappearsInRealActivity() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeTrue(context.packageName.endsWith("taskfixture"))
        val prefs = context.getSharedPreferences("companion", Context.MODE_PRIVATE)
        val updates = context.getSharedPreferences("updates", Context.MODE_PRIVATE)
        prefs.edit().clear().commit()
        updates.edit().putLong("lastAttempt", System.currentTimeMillis()).commit()
        val cert = HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").build()
        val server = MockWebServer()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory(), false)
        val removed = AtomicBoolean(false)
        val emptyHistoryReads = AtomicInteger()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = when {
                    request.path?.startsWith("/api/status") == true -> """{"threadId":"synthetic-history-audit","title":"Синтетический аудит","cwd":"/synthetic","status":"idle"}"""
                    request.path?.startsWith("/api/history") == true -> if (removed.get()) {
                        emptyHistoryReads.incrementAndGet(); """{"turns":[],"hasMore":false}"""
                    } else """{"turns":[{"id":"removed:user:0","turnId":"removed","role":"user","text":"OLD-REVERTED-SYNTHETIC"}],"hasMore":false}"""
                    request.path?.startsWith("/api/capabilities") == true -> """{"protocolVersion":1,"features":{"workspacePaging":true,"controls":true}}"""
                    request.path?.startsWith("/api/events") == true -> """{"cursor":"${if (removed.get()) "after" else "before"}"}"""
                    request.path?.startsWith("/api/projects") == true -> """{"projects":[],"selectedThreadId":"synthetic-history-audit"}"""
                    request.path?.startsWith("/api/models") == true -> """{"models":[]}"""
                    request.path?.startsWith("/api/workspace") == true -> """{"rootName":"Synthetic","entries":[],"path":""}"""
                    else -> """{"data":[],"cancelledIds":[],"receipts":{}}"""
                }
                return MockResponse().setBody(body).setHeader("Content-Type", "application/json")
                    .apply { if (request.path?.startsWith("/api/events") == true) setBodyDelay(300, TimeUnit.MILLISECONDS) }
            }
        }
        server.start()
        val pin = MessageDigest.getInstance("SHA-256").digest(cert.certificate.encoded).joinToString("") { "%02X".format(it) }
        prefs.edit().putString("host", server.url("/").toString().trimEnd('/')).putString("certificatePin", pin)
            .putString("selectedThreadId", "synthetic-history-audit").putString("themeMode", "light").commit()
        saveToken(context, prefs, "synthetic-audit-token")
        val activity = ActivityScenario.launch(MainActivity::class.java)
        try {
            compose.waitUntil(10000) { compose.onAllNodesWithText("OLD-REVERTED-SYNTHETIC").fetchSemanticsNodes().isNotEmpty() }
            removed.set(true)
            compose.waitUntil(10000) { emptyHistoryReads.get() > 0 }
            Thread.sleep(1000)
            compose.waitUntil(5000) { compose.onAllNodesWithText("OLD-REVERTED-SYNTHETIC").fetchSemanticsNodes().isEmpty() }
            Log.i("RV-AUDIT", "Server deletion removes old displayed message")
        } finally { activity.close(); prefs.edit().clear().commit(); updates.edit().clear().commit(); server.shutdown() }
    }
}
