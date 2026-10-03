package ru.wilmain.codexphone

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import okhttp3.mockwebserver.*
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicInteger

@RunWith(AndroidJUnit4::class)
class TaskActionsUiTest {
    @get:Rule val compose = createComposeRule()
    private fun shot(name: String) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        File(context.filesDir, "$name.png").outputStream().use { stream ->
            compose.onAllNodes(isRoot()).onLast().captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, stream)
        }
    }

    @Test fun questionRequiresExplicitAnswerAndPreservesDraft() {
        var response: JSONObject? = null
        val prompt = JSONObject("""{"id":"q1","method":"item/tool/requestUserInput","params":{"questions":[{"id":"design","question":"Какой вариант оформления применить?","isOther":true,"options":[{"label":"Фокус","description":"Нейтральный интерфейс без рамок"},{"label":"Компактный","description":"Больше элементов на экране"}]}]}}""")
        compose.setContent { TestTaskTheme { Column(Modifier.padding(24.dp)) { RequestBody(prompt, false, {}) { response = it } } } }
        compose.onNodeWithText("Отправить ответ").assertIsNotEnabled()
        compose.onNodeWithText("Фокус").performClick()
        compose.onNodeWithText("Отправить ответ").assertIsEnabled()
        shot("task-question-light")
        compose.onNodeWithText("Отправить ответ").performClick()
        assertEquals("Фокус", response?.getJSONObject("answers")?.getJSONObject("design")?.getJSONArray("answers")?.getString(0))
    }

    @Test fun permissionRequestExplainsScopeAndDeclines() {
        var response: JSONObject? = null
        compose.setContent { TestTaskTheme(dark = true) { Column(Modifier.padding(24.dp)) {
            RequestBody(JSONObject("""{"id":"p","method":"item/permissions/requestApproval","params":{"reason":"Загрузить зависимости проекта","permissions":{"network":{"enabled":true},"fileSystem":null}}}"""), false, {}) { response = it }
        } } }
        compose.onNodeWithText("Разрешение действует только на эту задачу.").assertIsDisplayed()
        shot("task-permission-dark")
        compose.onNodeWithText("Отклонить").performClick()
        assertEquals("decline", response?.optString("decision"))
    }

    @Test fun stopCapturesExpectedTurnAndRequiresConfirmation() {
        var payload: JSONObject? = null
        compose.setContent { TestTaskTheme { Column { TaskControls("root", "current", { path, body ->
            if (path == "turn/interrupt") payload = body
            JSONObject("""{"requests":[],"accepted":true}""")
        }, {}) } } }
        compose.onNodeWithText("Остановить").performClick()
        compose.onNodeWithText("Остановить задачу?").assertIsDisplayed()
        assertNull(payload)
        shot("task-stop-confirm")
        compose.onAllNodesWithText("Остановить").onLast().performClick()
        compose.waitUntil { payload != null }
        assertEquals("current", payload?.optString("turnId"))
        assertEquals("root", payload?.optString("threadId"))
    }

    @Test fun diffHasCountersFolderActionAndCopy() {
        var opened = ""
        compose.setContent { TestTaskTheme { ChangesScreen("root", { _, _ ->
            JSONObject("""{"changes":[{"id":"d","path":"src/Main.kt","diff":"@@ -1 +1 @@\n-old value\n+new value","truncated":false}]}""")
        }, {}, { opened = it }) } }
        compose.waitUntil { compose.onAllNodesWithText("src/Main.kt").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("+1 −1").assertIsDisplayed()
        compose.onNodeWithText("src/Main.kt").performClick()
        compose.onNodeWithText("Копировать diff").assertIsDisplayed().performClick()
        shot("task-diff-light")
        compose.onNodeWithText("Открыть папку").performClick()
        assertEquals("src/Main.kt", opened)
    }

    @Test fun requestReplyErrorKeepsPromptAndRetryWorks() {
        val attempts = AtomicInteger()
        compose.setContent { TestTaskTheme { Column { TaskControls("root", "", { path, _ ->
            if (path == "requests/respond") {
                if (attempts.incrementAndGet() == 1) error("offline")
                JSONObject("""{"accepted":true}""")
            } else if (attempts.get() >= 2) JSONObject("""{"requests":[]}""")
            else JSONObject("""{"requests":[{"id":"r","method":"item/commandExecution/requestApproval","params":{"command":"./gradlew assembleDebug","cwd":"/project","reason":"Собрать проект"}}]}""")
        }, {}) } } }
        compose.waitUntil { compose.onAllNodesWithText("Требуется ответ · 1").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Требуется ответ · 1").performClick()
        compose.onNodeWithText("Разрешить один раз").performClick()
        compose.waitUntil { compose.onAllNodes(hasText("Ответ не подтверждён", substring = true)).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Разрешить один раз").performClick()
        compose.waitUntil { attempts.get() == 2 }
    }

    @Test fun backgroundServiceNotifiesQuestionAndCompletionWithoutLiveCodex() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        org.junit.Assume.assumeTrue("Service test uses an isolated fixture APK", context.packageName.endsWith("taskfixture"))
        val cert = HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").build()
        val server = MockWebServer()
        server.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory(), false)
        val polls = AtomicInteger()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = when {
                    request.path?.startsWith("/api/task") == true -> if (polls.incrementAndGet() == 1) """{"found":true,"status":"inProgress"}"""
                        else """{"found":true,"status":"completed","outcomeSummary":"Выполнено · 12 с","quotaSummary":"1% за 5ч"}"""
                    else -> """{"requests":[{"id":"synthetic","method":"item/tool/requestUserInput"}]}"""
                }
                return MockResponse().setBody(body).setHeader("Content-Type", "application/json")
            }
        }
        server.start()
        val prefs = context.getSharedPreferences("companion", Context.MODE_PRIVATE)
        val pin = MessageDigest.getInstance("SHA-256").digest(cert.certificate.encoded).joinToString("") { "%02X".format(it) }
        prefs.edit().putString("host", server.url("/").toString().trimEnd('/')).putString("certificatePin", pin)
            .putBoolean("taskNotifications", true).apply()
        saveToken(context, prefs, "synthetic-token")
        try {
            TaskWatchService.watch(context, "synthetic-root", "synthetic-turn")
            val manager = context.getSystemService(NotificationManager::class.java)
            val deadline = System.currentTimeMillis() + 15000
            while (System.currentTimeMillis() < deadline && (manager.activeNotifications.none { it.id == "outcome:synthetic-turn".hashCode() } || manager.activeNotifications.any { it.id == 7001 })) Thread.sleep(100)
            assertTrue(manager.activeNotifications.any { it.id == "request:synthetic".hashCode() })
            assertTrue(manager.activeNotifications.any { it.id == "outcome:synthetic-turn".hashCode() })
            assertFalse(manager.activeNotifications.any { it.id == 7001 })
        } finally {
            context.stopService(Intent(context, TaskWatchService::class.java))
            context.getSystemService(NotificationManager::class.java).cancelAll()
            prefs.edit().clear().apply()
            server.shutdown()
        }
    }
}

@androidx.compose.runtime.Composable
private fun TestTaskTheme(dark: Boolean = false, content: @androidx.compose.runtime.Composable () -> Unit) {
    MaterialTheme(colorScheme = if (dark) darkPalette else lightPalette, typography = appTypography) {
        androidx.compose.material3.Surface(Modifier.fillMaxSize()) { content() }
    }
}
