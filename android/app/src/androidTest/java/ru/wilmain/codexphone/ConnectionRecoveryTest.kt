package ru.wilmain.codexphone

import androidx.test.ext.junit.runners.AndroidJUnit4
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class ConnectionRecoveryTest {
    @Test fun truncatedGetRetriesOnceOnFreshConnection() {
        val cert=HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val tls=HandshakeCertificates.Builder().heldCertificate(cert).build()
        val server=MockWebServer()
        server.useHttps(tls.sslSocketFactory(),false)
        server.enqueue(MockResponse().setBody("{\"status\":\""+"x".repeat(2048)+"\"}").setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY))
        server.enqueue(MockResponse().setBody("{\"status\":\"ready\"}"))
        server.start()
        try {
            val pin=MessageDigest.getInstance("SHA-256").digest(cert.certificate.encoded).joinToString(""){"%02x".format(it)}
            val client=pinnedClient(pin).newBuilder().retryOnConnectionFailure(false).build()
            assertEquals("ready",requestJson(client,server.url("/api/status").toString(),"synthetic-token").getString("status"))
            assertEquals(2,server.requestCount)
            repeat(2) {assertEquals("GET",server.takeRequest().method)}
        } finally {server.shutdown()}
    }

    @Test fun certificateFailuresNeverTriggerRecoveryRetry() {
        assertFalse(retryableReadFailure(javax.net.ssl.SSLHandshakeException("synthetic pin failure")))
        assertFalse(retryableReadFailure(javax.net.ssl.SSLPeerUnverifiedException("synthetic mismatch")))
        assertTrue(retryableReadFailure(java.io.EOFException("synthetic truncated read")))
        assertFalse(retryableReadFailure(java.net.SocketTimeoutException("synthetic timeout")))
    }
}
