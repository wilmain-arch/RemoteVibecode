package ru.wilmain.codexphone

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Material3UiTest {
    @get:Rule val compose = createComposeRule()
    private fun screenshot(name: String) {
        val file = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "fixture-$name.png")
        compose.onAllNodes(isRoot()).onLast().captureToImage().asAndroidBitmap().let { image ->
            file.outputStream().use { image.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        }
    }

    @Test fun queueCanSteerWithoutSendingNewTask() {
        var steered = false
        var cancelled = false
        compose.setContent {
            MaterialTheme(colorScheme = lightPalette, typography = appTypography) {
                CompanionUi(
                onUpdates = {},
                updateAvailable = false,
                paired = true,
                relayOnly = true,
                themeMode = "light",
                onThemeMode = {},
                title = "Обновление интерфейса",
                projectName = "Приложение",
                status = "Codex отвечает…",
                connectionState = ConnectionState.Online,
                usageLimits = UsageLimits(LimitWindow(88, 0), LimitWindow(98, 0), 0),
                limitsLoading = false,
                limitsError = "",
                lines = listOf(ChatLine("user", "Обнови интерфейс", "turn", id = "u"),
                    ChatLine("process", "Проверил компоненты", "turn", steps = 3, activities = listOf(ActivityItem("command", "Сборка APK", "completed")), id = "p"),
                    ChatLine("assistant", "Обновил компоновку. Добавил светлую и тёмную темы.", "turn", id = "a"),
                    ChatLine("outcome", "Готово", "turn", id = "o", outcomeSummary = "Готово · 2 мин 14 с", quotaSummary = "1% за 5ч")),
                queue = listOf(LocalMessage("queued", "Проверь тёмную тему", emptyList(), "test", acceptedByBridge = true)),
                selectedThreadId = "test",
                projects = emptyList(),
                catalogLoading = false,
                catalogError = "",
                models = listOf(ModelOption("gpt-test", "Тестовая модель", listOf("low", "high"), "high")),
                selectedModel = "gpt-test",
                selectedEffort = "high",
                modelOverridden = false,
                effortOverridden = false,
                input = "",
                attachments = emptyList(),
                outboxFiles = emptyList(),
                outboxLoading = false,
                outboxError = "",
                workspaceRoot = "",
                workspacePath = "",
                workspaceEntries = emptyList(),
                workspaceLoading = false,
                workspaceError = "",
                workspaceTruncated = false,
                previewPath = "",
                previewText = "",
                previewNote = "",
                previewLoading = false,
                fileStatus = "",
                transferringFileId = "",
                transferProgress = null,
                historyHasMore = false,
                historyLoading = false,
                historyError = "",
                initialHistoryLoading = false,
                initialHistoryError = "",
                onRetryHistory = {},
                onInput = {},
                onScan = {},
                onSelectThread = {},
                onDeleteThread = {},
                onMoveThread = { _, _ -> },
                onNewChat = {},
                onRefreshCatalog = {},
                onRefreshLimits = {},
                onResetLimits = {},
                resetMessage = "",
                resetLoading = false,
                resetPending = false,
                onSubagentRequest = { _, _ -> JSONObject().put("agents", org.json.JSONArray()) },
                onAdbRequest = { _, _ -> JSONObject() },
                onModel = {},
                onEffort = {},
                onAttach = {},
                onRemoveAttachment = {},
                onFetchFiles = {},
                onSaveFile = {},
                onBrowseWorkspace = {},
                onPreviewWorkspace = {},
                onAskWorkspace = {},
                onSaveWorkspace = {},
                onResolveProjectFile = { null },
                onSaveChatImage = {},
                onLoadOlder = {},
                onSend = {},
                onCancelQueued = { cancelled = true },
                onSteerQueued = { steered = true },
                onCancelTransfer = {},
                onDisconnect = {},
                loadImage = { fixtureImage() },
                )
            }
        }
        compose.onNode(hasSetTextAction()).performClick()
        compose.onAllNodesWithText("Корректировать").onFirst().assertIsDisplayed().performClick()
        compose.runOnIdle { check(steered) }
        compose.onNode(hasScrollToIndexAction()).performScrollToNode(hasText("Отменить"))
        compose.onNodeWithText("Отменить").performClick()
        compose.runOnIdle { check(cancelled) }
        screenshot("queue-ime")
    }

    @Test fun markdownLinksRouteAndCodeCopyIsExplicit() {
        var opened = ""
        compose.setContent {
            MaterialTheme(colorScheme = darkPalette, typography = appTypography) {
                androidx.compose.material3.Surface(color = MaterialTheme.colorScheme.background) {
                    MarkdownContent("[Файл](docs/README.md)\n\n```kotlin\nval a = 1\n```", onProjectFile = { opened = it })
                }
            }
        }
        compose.onNodeWithText("Файл").performTouchInput { click(androidx.compose.ui.geometry.Offset(12f, center.y)) }
        compose.runOnIdle { check(opened == "docs/README.md") }
        compose.onNodeWithContentDescription("Копировать код").assertIsDisplayed().performClick()
        compose.onNodeWithContentDescription("Скопировано").assertExists()
        screenshot("markdown")
    }

    @Test fun imageViewerHasSaveAndCloseEvenOnLoadFailure() {
        var saved = false
        var closed = false
        compose.setContent {
            MaterialTheme(colorScheme = darkPalette, typography = appTypography) {
                ImageViewer("fixture", "Очень длинное название изображения.png", { error("Тестовая ошибка") },
                    onClose = { closed = true }, onSave = { saved = true })
            }
        }
        compose.onNodeWithContentDescription("Скачать изображение").assertIsDisplayed().performClick()
        compose.runOnIdle { check(saved) }
        compose.onNodeWithText("Повторить").assertIsDisplayed()
        screenshot("image-error")
        compose.onNodeWithContentDescription("Назад").performClick()
        compose.runOnIdle { check(closed) }
    }

    @Test fun onboardingPreservesQrConnectionFlow() {
        compose.setContent {
            MaterialTheme(colorScheme = lightPalette, typography = appTypography) {
                CompanionUi(
                onUpdates = {},
                updateAvailable = false,
                paired = false,
                relayOnly = true,
                themeMode = "light",
                onThemeMode = {},
                title = "Обновление интерфейса",
                projectName = "Приложение",
                status = "Codex отвечает…",
                connectionState = ConnectionState.Online,
                usageLimits = UsageLimits(LimitWindow(88, 0), LimitWindow(98, 0), 0),
                limitsLoading = false,
                limitsError = "",
                lines = listOf(ChatLine("user", "Обнови интерфейс", "turn", id = "u"),
                    ChatLine("process", "Проверил компоненты", "turn", steps = 3, activities = listOf(ActivityItem("command", "Сборка APK", "completed")), id = "p"),
                    ChatLine("assistant", "Обновил компоновку. Добавил светлую и тёмную темы.", "turn", id = "a"),
                    ChatLine("outcome", "Готово", "turn", id = "o", outcomeSummary = "Готово · 2 мин 14 с", quotaSummary = "1% за 5ч")),
                queue = listOf(LocalMessage("queued", "Проверь тёмную тему", emptyList(), "test", acceptedByBridge = true)),
                selectedThreadId = "test",
                projects = emptyList(),
                catalogLoading = false,
                catalogError = "",
                models = listOf(ModelOption("gpt-test", "Тестовая модель", listOf("low", "high"), "high")),
                selectedModel = "gpt-test",
                selectedEffort = "high",
                modelOverridden = false,
                effortOverridden = false,
                input = "",
                attachments = emptyList(),
                outboxFiles = emptyList(),
                outboxLoading = false,
                outboxError = "",
                workspaceRoot = "",
                workspacePath = "",
                workspaceEntries = emptyList(),
                workspaceLoading = false,
                workspaceError = "",
                workspaceTruncated = false,
                previewPath = "",
                previewText = "",
                previewNote = "",
                previewLoading = false,
                fileStatus = "",
                transferringFileId = "",
                transferProgress = null,
                historyHasMore = false,
                historyLoading = false,
                historyError = "",
                initialHistoryLoading = false,
                initialHistoryError = "",
                onRetryHistory = {},
                onInput = {},
                onScan = {},
                onSelectThread = {},
                onDeleteThread = {},
                onMoveThread = { _, _ -> },
                onNewChat = {},
                onRefreshCatalog = {},
                onRefreshLimits = {},
                onResetLimits = {},
                resetMessage = "",
                resetLoading = false,
                resetPending = false,
                onSubagentRequest = { _, _ -> JSONObject().put("agents", org.json.JSONArray()) },
                onAdbRequest = { _, _ -> JSONObject() },
                onModel = {},
                onEffort = {},
                onAttach = {},
                onRemoveAttachment = {},
                onFetchFiles = {},
                onSaveFile = {},
                onBrowseWorkspace = {},
                onPreviewWorkspace = {},
                onAskWorkspace = {},
                onSaveWorkspace = {},
                onResolveProjectFile = { null },
                onSaveChatImage = {},
                onLoadOlder = {},
                onSend = {},
                onCancelQueued = {},
                onSteerQueued = {},
                onCancelTransfer = {},
                onDisconnect = {},
                loadImage = { fixtureImage() },
                )
            }
        }
        compose.onNodeWithText("Сканировать QR-код").assertIsDisplayed()
        compose.onNodeWithText("Подключите компьютер").assertIsDisplayed()
        screenshot("onboarding")
    }

    @Test fun fileSearchCanClearWhileKeyboardIsOpen() {
        compose.setContent {
            MaterialTheme(colorScheme = lightPalette, typography = appTypography) {
                WorkspaceFilesScreen(
                projectName = "Приложение",
                rootName = "Приложение",
                threadId = "test",
                path = "",
                entries = listOf(WorkspaceEntry("assets", "assets", true, 0), WorkspaceEntry("README.md", "README.md", false, 4096), WorkspaceEntry("app.kt", "app.kt", false, 12288), WorkspaceEntry("preview.png", "preview.png", false, 6144)),
                loading = false,
                error = "",
                truncated = false,
                previewPath = "",
                previewText = "",
                previewNote = "",
                previewLoading = false,
                fileStatus = "",
                outboxFiles = emptyList(),
                outboxLoading = false,
                outboxError = "",
                transferringFileId = "",
                transferProgress = null,
                pendingFiles = emptyList(),
                loadImage = { fixtureImage() },
                onCancelTransfer = {},
                onClose = {},
                onBrowse = {},
                onPreview = {},
                onAsk = {},
                onSaveWorkspace = {},
                onSaveOutbox = {},
                onFetchOutbox = {},
                onAttach = {},
                onProjectFile = {},
                )
            }
        }
        screenshot("focus-files")
        compose.onNode(hasSetTextAction()).performClick().performTextInput("no_match")
        compose.onNodeWithContentDescription("Проект").assertIsDisplayed()
        compose.onNodeWithContentDescription("Передача").assertIsDisplayed()
        compose.onNodeWithContentDescription("Очистить поиск").performClick()
        compose.onNodeWithText("README.md").assertIsDisplayed()
        screenshot("files-ime")
    }
    @Test fun subagentsRecoverFromIsolatedServerFailure() {
        val certificate = okhttp3.tls.HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").build()
        val serverTls = okhttp3.tls.HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val clientTls = okhttp3.tls.HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
        val server = okhttp3.mockwebserver.MockWebServer()
        server.useHttps(serverTls.sslSocketFactory(), false)
        server.start()
        val client = okhttp3.OkHttpClient.Builder().sslSocketFactory(clientTls.sslSocketFactory(), clientTls.trustManager)
            .readTimeout(2, java.util.concurrent.TimeUnit.SECONDS).retryOnConnectionFailure(false).build()
        val agents = """{"agents":[{"id":"test-agent","parentId":"test","name":"Gauss","model":"GPT-6.1 Sol","status":"idle","canSend":true,"task":"Проверка интерфейса"}]}"""
        server.enqueue(okhttp3.mockwebserver.MockResponse().setResponseCode(503).setBody("Изолированный сетевой отказ"))
        try {
            compose.setContent {
                MaterialTheme(colorScheme = darkPalette, typography = appTypography) {
                    SubagentsScreen("test", {}, request = { path, body ->
                        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                            val builder = okhttp3.Request.Builder().url(server.url("/api/$path"))
                            if (body != null) builder.post(okhttp3.RequestBody.create(null, body.toString()))
                            client.newCall(builder.build()).execute().use { response ->
                                check(response.isSuccessful) { "Сервер недоступен (${response.code})" }
                                JSONObject(response.body!!.string())
                            }
                        }
                    }, loadImage = { fixtureImage() }, onProjectFile = {}, onSaveImage = {})
                }
            }
            compose.waitUntil(5000) { compose.onAllNodesWithText("Не удалось обновить", substring = true).fetchSemanticsNodes().isNotEmpty() }
            screenshot("subagents-error")
            server.enqueue(okhttp3.mockwebserver.MockResponse().setBody(agents))
            compose.onNodeWithContentDescription("Обновить").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithText("Gauss").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("GPT-6.1 Sol").assertIsDisplayed()
            screenshot("subagents-recovered")
            // A real connection drop, then retry. Existing data must remain usable.
            server.enqueue(okhttp3.mockwebserver.MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AT_START))
            compose.onNodeWithContentDescription("Обновить").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithText("Не удалось обновить", substring = true).fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("Gauss").assertIsDisplayed()
            server.enqueue(okhttp3.mockwebserver.MockResponse().setBody(agents))
            compose.onNodeWithContentDescription("Обновить").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithText("Не удалось обновить", substring = true).fetchSemanticsNodes().isEmpty() }
        } finally { server.shutdown() }
    }

    @Composable private fun focusFixture(dark: Boolean = false) {
        var draft by androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf("") }
        MaterialTheme(colorScheme = if (dark) darkPalette else lightPalette, typography = appTypography) {
                CompanionUi(
                onUpdates = {},
                updateAvailable = false,
                paired = true,
                relayOnly = true,
                themeMode = if (dark) "dark" else "light",
                onThemeMode = {},
                title = "Обновление интерфейса",
                projectName = "Приложение",
                status = "На связи",
                connectionState = ConnectionState.Online,
                usageLimits = UsageLimits(LimitWindow(88, 0), LimitWindow(98, 0), 0),
                limitsLoading = false,
                limitsError = "",
                lines = listOf(ChatLine("user", "Обнови интерфейс", "turn", id = "u"),
                    ChatLine("process", "Проверил компоненты", "turn", steps = 3, activities = listOf(ActivityItem("command", "Сборка APK", "completed")), id = "p"),
                    ChatLine("assistant", "Обновил компоновку. Добавил светлую и тёмную темы.", "turn", id = "a"),
                    ChatLine("outcome", "Готово", "turn", id = "o", outcomeSummary = "Готово · 2 мин 14 с", quotaSummary = "1% за 5ч")),
                queue = emptyList(),
                selectedThreadId = "test",
                projects = listOf(ProjectGroup("app", "Приложение", "/fixture", listOf(ThreadItem("test", "Обновление интерфейса", "idle", 0))), ProjectGroup("work", "Работа", "/work", emptyList())),
                catalogLoading = false,
                catalogError = "",
                models = listOf(ModelOption("gpt-test", "GPT-6.1", listOf("low", "high"), "high")),
                selectedModel = "gpt-test",
                selectedEffort = "high",
                modelOverridden = false,
                effortOverridden = false,
                input = draft,
                attachments = emptyList(),
                outboxFiles = emptyList(),
                outboxLoading = false,
                outboxError = "",
                workspaceRoot = "",
                workspacePath = "",
                workspaceEntries = emptyList(),
                workspaceLoading = false,
                workspaceError = "",
                workspaceTruncated = false,
                previewPath = "",
                previewText = "",
                previewNote = "",
                previewLoading = false,
                fileStatus = "",
                transferringFileId = "",
                transferProgress = null,
                historyHasMore = false,
                historyLoading = false,
                historyError = "",
                initialHistoryLoading = false,
                initialHistoryError = "",
                onRetryHistory = {},
                onInput = { draft = it },
                onScan = {},
                onSelectThread = {},
                onDeleteThread = {},
                onMoveThread = { _, _ -> },
                onNewChat = {},
                onRefreshCatalog = {},
                onRefreshLimits = {},
                onResetLimits = {},
                resetMessage = "",
                resetLoading = false,
                resetPending = false,
                onSubagentRequest = { _, _ -> JSONObject().put("agents", org.json.JSONArray()) },
                onAdbRequest = { _, _ -> JSONObject() },
                onModel = {},
                onEffort = {},
                onAttach = {},
                onRemoveAttachment = {},
                onFetchFiles = {},
                onSaveFile = {},
                onBrowseWorkspace = {},
                onPreviewWorkspace = {},
                onAskWorkspace = {},
                onSaveWorkspace = {},
                onResolveProjectFile = { null },
                onSaveChatImage = {},
                onLoadOlder = {},
                onSend = {},
                onCancelQueued = {},
                onSteerQueued = {},
                onCancelTransfer = {},
                onDisconnect = {},
                loadImage = { fixtureImage() },
                )
        }
    }
    @Test fun focusChatLightAndOverflowNavigation() {
        val restore = androidx.compose.ui.test.junit4.StateRestorationTester(compose)
        restore.setContent { focusFixture() }
        screenshot("focus-chat-light")
        compose.onNodeWithContentDescription("Ход работы, свёрнут").performClick()
        compose.onNodeWithContentDescription("Сборка APK, завершено").performScrollTo().assertIsDisplayed()
        restore.emulateSavedInstanceStateRestore()
        compose.onNodeWithContentDescription("Сборка APK, завершено").performScrollTo().assertIsDisplayed()
        compose.onNodeWithContentDescription("Меню чата").performClick()
        compose.onNodeWithText("Субагенты").assertIsDisplayed()
        compose.onNodeWithText("Новый чат").assertIsDisplayed()
        screenshot("focus-overflow")
    }
    @Test fun focusChatDarkAndDraftRestore() {
        val restore = androidx.compose.ui.test.junit4.StateRestorationTester(compose)
        restore.setContent { focusFixture(true) }
        screenshot("focus-chat-dark")
        compose.onNode(hasSetTextAction()).performTextInput("Сделай заголовки выразительнее.\nСохрани все функции.")
        restore.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("Сделай заголовки выразительнее.\nСохрани все функции.").assertExists()
        screenshot("focus-multiline")
    }
    @Test fun focusProjectsSettingsAndRestore() {
        val restore = androidx.compose.ui.test.junit4.StateRestorationTester(compose)
        restore.setContent { focusFixture() }
        compose.onNodeWithContentDescription("Проекты и чаты").performClick()
        screenshot("focus-projects")
        compose.onNodeWithText("Настройки").performClick()
        compose.onNodeWithContentDescription("Тема: Светлая").assertIsDisplayed()
        compose.onNodeWithText("ADB").assertIsDisplayed()
        restore.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("ADB").assertIsDisplayed()
        screenshot("focus-settings")
    }
    @Test fun focusLargeFontRetainsActions() {
        compose.setContent {
            val density = androidx.compose.ui.platform.LocalDensity.current
            CompositionLocalProvider(androidx.compose.ui.platform.LocalDensity provides androidx.compose.ui.unit.Density(density.density, 2f)) {
                focusFixture()
            }
        }
        compose.onNodeWithContentDescription("Меню чата").assertIsDisplayed()
        compose.onNodeWithContentDescription("Файлы проекта").assertIsDisplayed()
        compose.onNodeWithContentDescription("Прикрепить файл").assertIsDisplayed()
        screenshot("focus-font200")
    }

    private fun fixtureImage(): ByteArray {
        val bitmap = android.graphics.Bitmap.createBitmap(1200, 800, android.graphics.Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bitmap)
        canvas.drawColor(android.graphics.Color.rgb(36, 42, 37))
        canvas.drawCircle(600f, 400f, 240f, android.graphics.Paint().apply { color = android.graphics.Color.rgb(144, 212, 187); isAntiAlias = true })
        return java.io.ByteArrayOutputStream().also { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
    }
    @Test fun focusViewerPinchAndSave() {
        var saved = false
        val bytes = fixtureImage()
        compose.setContent {
            MaterialTheme(colorScheme = darkPalette, typography = appTypography) {
                ImageViewer("synthetic", "preview.png", { bytes }, {}, {
                    File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "download-fixture.png").writeBytes(bytes)
                    saved = true
                })
            }
        }
        compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("preview.png").fetchSemanticsNodes().isNotEmpty() }
        screenshot("focus-preview")
        compose.onNodeWithContentDescription("preview.png").performTouchInput {
            pinch(center - androidx.compose.ui.geometry.Offset(80f, 0f), center + androidx.compose.ui.geometry.Offset(80f, 0f),
                center - androidx.compose.ui.geometry.Offset(240f, 0f), center + androidx.compose.ui.geometry.Offset(240f, 0f))
        }
        compose.onNodeWithText("Вернуть исходный размер").assertIsDisplayed()
        screenshot("focus-preview-pinch")
        compose.onNodeWithContentDescription("Скачать изображение").performClick()
        compose.runOnIdle { check(saved) }
        check(File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "download-fixture.png").readBytes().contentEquals(bytes))
    }

}
