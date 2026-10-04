package ru.wilmain.codexphone

import android.content.Context
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.util.Base64
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.ByteArrayOutputStream
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.Typography
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Call
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.UUID
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning

private fun readPendingFiles(prefs: android.content.SharedPreferences): List<PendingFile> = runCatching {
    val array = JSONArray(prefs.getString("stagedFiles", "[]") ?: "[]")
    (0 until array.length()).mapNotNull { index ->
        val item = array.optJSONObject(index) ?: return@mapNotNull null
        PendingFile(item.getString("id"), item.getString("name"))
    }
}.getOrDefault(emptyList())

private fun savePendingFiles(prefs: android.content.SharedPreferences, files: List<PendingFile>) {
    val array = JSONArray()
    files.forEach { array.put(JSONObject().put("id", it.id).put("name", it.name)) }
    prefs.edit().putString("stagedFiles", array.toString()).apply()
}
private fun savePendingDeletes(prefs: android.content.SharedPreferences, ids: List<String>) {
    prefs.edit().putStringSet("pendingFileDeletes", ids.toSet()).apply()
}
private fun readLocalQueue(prefs: android.content.SharedPreferences): List<LocalMessage> = runCatching {
    val array = JSONArray(prefs.getString("localQueue", "[]") ?: "[]")
    (0 until array.length()).mapNotNull { index ->
        val item = array.optJSONObject(index) ?: return@mapNotNull null
        val fileArray = item.optJSONArray("files") ?: JSONArray()
        LocalMessage(item.getString("id"), item.optString("text"),
            (0 until fileArray.length()).map { fileArray.getString(it) },
            item.optString("threadId"),
            if (item.isNull("model")) null else item.optString("model").ifBlank { null },
            if (item.isNull("effort")) null else item.optString("effort").ifBlank { null },
            item.optBoolean("acceptedByBridge"), item.optBoolean("cancelRequested"),
            item.optString("deliveredTurnId"), item.optBoolean("steered"), item.optString("nativeSubmissionId"), item.optString("queueState", "queued"))
    }
}.getOrDefault(emptyList())

private fun saveLocalQueue(prefs: android.content.SharedPreferences, messages: List<LocalMessage>) {
    val array = JSONArray()
    messages.forEach { message ->
        array.put(JSONObject().put("id", message.id).put("text", message.text)
            .put("files", JSONArray(message.files))
            .put("threadId", message.threadId).put("model", message.model)
            .put("effort", message.effort)
            .put("acceptedByBridge", message.acceptedByBridge)
            .put("cancelRequested", message.cancelRequested)
            .put("deliveredTurnId", message.deliveredTurnId)
            .put("steered", message.steered).put("nativeSubmissionId", message.nativeSubmissionId).put("queueState",message.queueState))
    }
    prefs.edit().putString("localQueue", array.toString()).apply()
}
private data class PairResult(
    val host: String, val tailHost: String, val activeHost: String,
    val fingerprint: String, val token: String, val threadId: String
)

private suspend fun pairFromCode(uri: Uri): PairResult {
    require(uri.scheme == "codexphone" && uri.host == "pair") { "Это не код приложения" }
    val host = uri.getQueryParameter("host") ?: error("Нет адреса ПК")
    val tailHost = uri.getQueryParameter("tailHost") ?: ""
    if (BuildConfig.RELAY_ONLY) {
        require(tailHost.isBlank()) { "Нужен QR-код агента для ретранслятора" }
    }
    val pin = uri.getQueryParameter("pin") ?: error("Нет кода привязки")
    val fingerprint = (uri.getQueryParameter("fingerprint") ?: "")
        .filter { it.isLetterOrDigit() }.uppercase()
    require(host.startsWith("https://") && fingerprint.length == 64) { "Недействительный код привязки" }
    val client = pinnedClient(fingerprint)
    val body = JSONObject().put("pin", pin).toString().toRequestBody("application/json".toMediaType())
    var lastError: Throwable? = null
    for (candidate in (if (BuildConfig.RELAY_ONLY) listOf(host) else listOf(host, tailHost))
        .filter { it.isNotBlank() }) {
        try {
            val request = Request.Builder().url(candidate.trimEnd('/') + "/api/pair").post(body).build()
            val result = withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    val json = JSONObject(response.body?.string().orEmpty())
                    if (!response.isSuccessful) error(json.optString("error", "Привязка не удалась"))
                    json
                }
            }
            return PairResult(host, tailHost, candidate, fingerprint,
                result.getString("token"), result.optString("threadId"))
        } catch (error: Exception) {
            lastError = error
        }
    }
    throw lastError ?: IllegalStateException("ПК недоступен")
}
internal val lightPalette = lightColorScheme(
    primary = Color(0xFF202321),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDFE4DD),
    onPrimaryContainer = Color(0xFF202321),
    secondary = Color(0xFF247661),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE5F1EA),
    onSecondaryContainer = Color(0xFF17634F),
    tertiary = Color(0xFF565D58),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFE5E9E3),
    onTertiaryContainer = Color(0xFF202321),
    inverseSurface = Color(0xFF2A312B),
    inverseOnSurface = Color(0xFFF2F4F1),
    inversePrimary = Color(0xFFDFE4DD),
    background = Color(0xFFF8F9F7),
    onBackground = Color(0xFF202321),
    surface = Color(0xFFF8F9F7),
    onSurface = Color(0xFF202321),
    surfaceVariant = Color(0xFFEBEEEA),
    onSurfaceVariant = Color(0xFF565D58),
    surfaceDim = Color(0xFFD9DED8),
    surfaceBright = Color(0xFFF8F9F7),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF2F4F0),
    surfaceContainer = Color(0xFFEBEEEA),
    surfaceContainerHigh = Color(0xFFE5E9E3),
    surfaceContainerHighest = Color(0xFFDFE4DD),
    surfaceTint = Color(0xFF202321),
    outline = Color(0xFF6C746D),
    outlineVariant = Color(0xFFE4E7E3),
)
internal val darkPalette = darkColorScheme(
    primary = Color(0xFFF2F4F1),
    onPrimary = Color(0xFF141715),
    primaryContainer = Color(0xFF343B35),
    onPrimaryContainer = Color(0xFFF2F4F1),
    secondary = Color(0xFF90D4BB),
    onSecondary = Color(0xFF141715),
    secondaryContainer = Color(0xFF20382E),
    onSecondaryContainer = Color(0xFF90D4BB),
    tertiary = Color(0xFFA0A8A2),
    onTertiary = Color(0xFF141715),
    tertiaryContainer = Color(0xFF2A312B),
    onTertiaryContainer = Color(0xFFF2F4F1),
    inverseSurface = Color(0xFFEBEEEA),
    inverseOnSurface = Color(0xFF202321),
    inversePrimary = Color(0xFF202321),
    background = Color(0xFF141715),
    onBackground = Color(0xFFF2F4F1),
    surface = Color(0xFF141715),
    onSurface = Color(0xFFF2F4F1),
    surfaceVariant = Color(0xFF242A25),
    onSurfaceVariant = Color(0xFFA0A8A2),
    surfaceDim = Color(0xFF141715),
    surfaceBright = Color(0xFF343B35),
    surfaceContainerLowest = Color(0xFF0F1210),
    surfaceContainerLow = Color(0xFF1A1F1B),
    surfaceContainer = Color(0xFF202621),
    surfaceContainerHigh = Color(0xFF2A312B),
    surfaceContainerHighest = Color(0xFF343B35),
    surfaceTint = Color(0xFFF2F4F1),
    outline = Color(0xFF7A857D),
    outlineVariant = Color(0xFF303632),
)
internal val appTypography = Typography().let { base ->
    base.copy(headlineSmall = base.headlineSmall.copy(fontSize = 24.sp, lineHeight = 30.sp),
        bodyLarge = base.bodyLarge.copy(fontSize = 16.sp, lineHeight = 25.sp),
        bodyMedium = base.bodyMedium.copy(fontSize = 14.sp, lineHeight = 20.sp),
        bodySmall = base.bodySmall.copy(fontSize = 13.sp, lineHeight = 18.sp),
        labelSmall = base.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp),
        labelMedium = base.labelMedium.copy(fontSize = 13.sp, lineHeight = 18.sp))
}

class MainActivity : ComponentActivity() {
    private var pairingUri by mutableStateOf<Uri?>(null)
    private var openThreadId by mutableStateOf("")
    private var notificationNonce by mutableStateOf(0)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (android.os.Build.VERSION.SDK_INT >= 29) window.isNavigationBarContrastEnforced = false
        pairingUri = intent?.data
        openThreadId = intent?.getStringExtra("openThreadId").orEmpty()
        setContent { CompanionScreen(pairingUri, openThreadId, notificationNonce) }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairingUri = intent.data
        openThreadId = intent.getStringExtra("openThreadId").orEmpty()
        notificationNonce++
    }
}

@Composable
private fun CompanionScreen(externalPairingUri: Uri?, notificationThreadId: String = "", notificationNonce: Int = 0) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("companion", Context.MODE_PRIVATE) }
    var taskNotifications by remember { mutableStateOf(prefs.getBoolean("taskNotifications", false)) }
    var activeTurnId by remember { mutableStateOf("") }
    val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        taskNotifications = granted
        prefs.edit().putBoolean("taskNotifications", granted).apply()
    }
    var themeMode by remember { mutableStateOf(prefs.getString("themeMode", "system") ?: "system") }
    val scope = rememberCoroutineScope()
    val updates = remember { AppUpdates(context.applicationContext, scope) }
    var updatesOpen by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(updates) {
        while (true) {
            updates.check(manual = false)
            delay(3_600_000)
        }
    }
    var certificatePin by remember { mutableStateOf(prefs.getString("certificatePin", "") ?: "") }
    val client = remember(certificatePin) { pinnedClient(certificatePin) }
    var host by remember { mutableStateOf(prefs.getString("host", "") ?: "") }
    var tailHost by remember { mutableStateOf(prefs.getString("tailHost", "") ?: "") }
    var activeHost by remember { mutableStateOf(host) }
    var token by remember { mutableStateOf(readSavedToken(context, prefs)) }
    var title by remember { mutableStateOf("Не подключено") }
    var projectName by remember { mutableStateOf("") }
    var status by remember { mutableStateOf(if (token.isBlank())
        (if (BuildConfig.RELAY_ONLY) "Сканируй QR-код агента на ПК" else "Сканируй QR-код на ПК один раз")
        else "Подключаюсь…") }
    var resetLoading by remember { mutableStateOf(false) }
    var resetMessage by remember { mutableStateOf("") }
    var resetRetryKey by remember { mutableStateOf(prefs.getString("resetRetryKey", null)) }
    var usageLimits by remember { mutableStateOf<UsageLimits?>(null) }
    var limitsLoading by remember { mutableStateOf(false) }
    var limitsError by remember { mutableStateOf("") }
    LaunchedEffect(taskNotifications, token) {
        if (taskNotifications && token.isNotBlank()) {
            runCatching { TaskWatchService.restore(context) }
        }
    }
    var connectionState by remember { mutableStateOf(ConnectionState.Connecting) }
    var codexBusy by remember { mutableStateOf(false) }
    var selectedThreadId by remember { mutableStateOf(prefs.getString("selectedThreadId", "") ?: "") }
    var threadModel by remember { mutableStateOf("") }
    var threadEffort by remember { mutableStateOf("") }
    var selectedModel by remember { mutableStateOf("") }
    var selectedEffort by remember { mutableStateOf("") }
    val models = remember { mutableStateListOf<ModelOption>() }
    val projects = remember { mutableStateListOf<ProjectGroup>() }
    var catalogLoading by remember { mutableStateOf(false) }
    var catalogError by remember { mutableStateOf("") }
    var input by rememberSaveable { mutableStateOf(prefs.getString("draft:${selectedThreadId}", "") ?: "") }
    var delivering by remember { mutableStateOf(false) }
    var localQueueError by remember { mutableStateOf("") }
    var lines by remember { mutableStateOf<List<ChatLine>>(emptyList()) }
    var historyHasMore by remember { mutableStateOf(false) }
    var olderPagesLoaded by remember { mutableStateOf(false) }
    var historyGeneration by remember { mutableIntStateOf(0) }
    var loadingOlder by remember { mutableStateOf(false) }
    var historyError by remember { mutableStateOf("") }
    var historyReady by remember { mutableStateOf(false) }
    var initialHistoryLoading by remember { mutableStateOf(token.isNotBlank()) }
    var initialHistoryError by remember { mutableStateOf("") }
    val files = remember { mutableStateListOf<PendingFile>().apply { addAll(readPendingFiles(prefs)) } }
    val pendingFileDeletes = remember { mutableStateListOf<String>().apply {
        addAll(prefs.getStringSet("pendingFileDeletes", emptySet()).orEmpty())
    } }
    var desktopQueue by remember { mutableStateOf(emptyList<LocalMessage>()) }
    val localQueue = remember { mutableStateListOf<LocalMessage>().apply { addAll(readLocalQueue(prefs)) } }
    val outboxFiles = remember { mutableStateListOf<RemoteFile>() }
    var outboxLoading by remember { mutableStateOf(false) }
    var outboxError by remember { mutableStateOf("") }
    var workspaceAdvanced by remember { mutableStateOf(false) }
    var compatibilityNote by remember { mutableStateOf("Проверяем совместимость агента…") }
    var workspaceNextCursor by remember { mutableStateOf("") }
    var workspaceRevision by remember { mutableIntStateOf(0) }
    val workspacePrefs = remember { context.getSharedPreferences("workspace-display", 0) }
    var workspaceRoot by remember { mutableStateOf("") }
    var workspaceThreadId by rememberSaveable { mutableStateOf(selectedThreadId) }
    var workspacePath by rememberSaveable { mutableStateOf("") }
    var workspaceEntries by remember { mutableStateOf<List<WorkspaceEntry>>(emptyList()) }
    var workspaceLoading by remember { mutableStateOf(false) }
    var workspaceError by remember { mutableStateOf("") }
    var workspaceTruncated by remember { mutableStateOf(false) }
    var previewPath by rememberSaveable { mutableStateOf("") }
    var previewText by remember { mutableStateOf("") }
    var previewNote by remember { mutableStateOf("") }
    var previewLoading by remember { mutableStateOf(false) }
    var transferProgress by remember { mutableStateOf<Pair<Long, Long>?>(null) }
    var transferringFileId by remember { mutableStateOf("") }
    var fileStatus by remember { mutableStateOf("") }
    var selectedOutbox by remember { mutableStateOf<RemoteFile?>(null) }
    var selectedChatImage by remember { mutableStateOf<ChatImage?>(null) }
    var activeUploadJob by remember { mutableStateOf<Job?>(null) }
    var activeDownloadJob by remember { mutableStateOf<Job?>(null) }
    val activeTransferCall = remember { AtomicReference<Call?>() }

    fun parseHistory(history: JSONObject): List<ChatLine> {
        val arr = history.optJSONArray("turns") ?: JSONArray()
        return (0 until arr.length()).mapNotNull { i ->
            val item = arr.optJSONObject(i) ?: return@mapNotNull null
            val text = item.optString("text")
            val role = item.optString("role", "assistant")
            val activityArray = item.optJSONArray("activities") ?: JSONArray()
            val activities = (0 until activityArray.length()).mapNotNull { j ->
                val activity = activityArray.optJSONObject(j) ?: return@mapNotNull null
                ActivityItem(activity.optString("kind"), activity.optString("label"), activity.optString("status"))
            }
            val attachmentArray = item.optJSONArray("attachments") ?: JSONArray()
            val attachments = (0 until attachmentArray.length()).map { attachmentArray.optString(it) }
            val imageArray = item.optJSONArray("images") ?: JSONArray()
            val images = (0 until imageArray.length()).mapNotNull { index ->
                imageArray.optJSONObject(index)?.let { ChatImage(it.optString("id"), it.optString("name")) }
            }
            if (text.isBlank() && activities.isEmpty() && attachments.isEmpty() && images.isEmpty()) null else ChatLine(
                role, text, item.optString("turnId"), item.optInt("steps"), activities,
                item.optString("id"), item.optString("time"), attachments, images,
                item.optString("outcomeSummary"), item.optString("quotaSummary"),
                if (item.isNull("clientMessageId")) "" else item.optString("clientMessageId"))
        }
    }

    suspend fun <T> withReachableHost(block: suspend (String) -> T): T {
        val choices = (if (BuildConfig.RELAY_ONLY) listOf(host)
            else listOf(activeHost, host, tailHost)).filter { it.isNotBlank() }.distinct()
        var lastError: Throwable? = null
        for (candidate in choices) {
            try {
                val result = block(candidate.trimEnd('/'))
                activeHost = candidate
                return result
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                currentCoroutineContext().ensureActive()
                lastError = error
            }
        }
        throw lastError ?: IllegalStateException("Не задан адрес ПК")
    }

    suspend fun fetchImage(endpoint: String): ByteArray = withReachableHost { root ->
        withContext(Dispatchers.IO) {
            val request = Request.Builder().url(root + endpoint)
                .header("Authorization", "Bearer $token").get().build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) error("ПК вернул ошибку ${response.code}")
                val limit = 20 * 1024 * 1024
                val body = response.body ?: error("Пустой ответ")
                if (body.contentLength() > limit) error("Изображение слишком большое")
                val output = ByteArrayOutputStream()
                body.byteStream().use { stream ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val count = stream.read(buffer)
                        if (count < 0) break
                        if (output.size() + count > limit) error("Изображение слишком большое")
                        output.write(buffer, 0, count)
                    }
                }
                output.toByteArray()
            }
        }
    }

    suspend fun cleanupPendingFiles() {
        for (id in pendingFileDeletes.toList()) {
            val body = JSONObject().put("id", id).toString()
                .toRequestBody("application/json".toMediaType())
            withReachableHost { root -> withContext(Dispatchers.IO) {
                val request = Request.Builder().url("$root/api/upload/cancel")
                    .header("Authorization", "Bearer $token").post(body).build()
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error("Не удалось удалить вложение")
                }
            } }
            pendingFileDeletes.remove(id)
            savePendingDeletes(prefs, pendingFileDeletes.toList())
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null && activeUploadJob == null && activeDownloadJob == null) {
            val uploadId = UUID.randomUUID().toString().replace("-", "")
            activeUploadJob = scope.launch {
            status = "Передаю файл…"
            fileStatus = "Передаю файл…"
            transferringFileId = "upload"
            try {
                val file = withReachableHost { url -> uploadFile(context, client, url, token, uri,
                    uploadId, activeTransferCall) }
                files.add(file)
                savePendingFiles(prefs, files.toList())
                status = "Файл готов: ${file.name}"
                fileStatus = "Готово к отправке: ${file.name}"
            } catch (cancelled: CancellationException) {
                pendingFileDeletes.add(uploadId)
                savePendingDeletes(prefs, pendingFileDeletes.toList())
                status = "Передача отменена"
                fileStatus = status
            } catch (error: Exception) {
                status = "Ошибка передачи: ${error.message}"
                fileStatus = status
            } finally {
                transferringFileId = ""
                activeUploadJob = null
            }
            }
        }
    }
    val savePicker = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("*/*")) { uri ->
        val file = selectedOutbox
        if (uri != null && file != null && activeDownloadJob == null && activeUploadJob == null) {
            activeDownloadJob = scope.launch {
            status = "Скачиваю ${file.name}…"
            fileStatus = status
            transferringFileId = file.workspacePath.ifBlank { file.id }
            transferProgress = 0L to file.size
            try {
                withReachableHost { root -> downloadFile(context, client, root, token, file, uri,
                    activeTransferCall) { done, total ->
                    transferProgress = done to total
                } }
                status = "Файл сохранён: ${file.name}"
                fileStatus = status
            } catch (cancelled: CancellationException) {
                runCatching { context.contentResolver.delete(uri, null, null) }
                status = "Скачивание отменено"
                fileStatus = status
            } catch (error: Exception) {
                runCatching { context.contentResolver.delete(uri, null, null) }
                status = "Не удалось сохранить файл: ${error.message}"
                fileStatus = status
            } finally {
                transferringFileId = ""
                transferProgress = null
                activeDownloadJob = null
            }
            }
        }
    }
    val saveChatImagePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("image/*")) { uri ->
        val image = selectedChatImage
        if (uri != null && image != null) {
            scope.launch {
                status = "Сохраняю ${image.name}…"
                runCatching {
                    val bytes = fetchImage("/api/chat/image?id=${Uri.encode(image.id)}")
                    withContext(Dispatchers.IO) {
                        context.contentResolver.openOutputStream(uri, "wt")?.use { it.write(bytes) }
                            ?: error("Не удалось открыть выбранный файл")
                    }
                }.onSuccess { status = "Изображение сохранено: ${image.name}" }
                    .onFailure {
                        runCatching { context.contentResolver.delete(uri, null, null) }
                        status = "Не удалось сохранить изображение: ${it.message}"
                    }
            }
        }
    }

    suspend fun refresh() {
        if (token.isBlank()) return
        val requestedThreadId = selectedThreadId
        val requestedGeneration = historyGeneration
        val threadQuery = if (requestedThreadId.isBlank()) "" else "?threadId=${Uri.encode(requestedThreadId)}"
        if (!historyReady) {
            initialHistoryLoading = true
            initialHistoryError = ""
        }
        try {
            val pair = withReachableHost { root ->
                withContext(Dispatchers.IO) {
                    val caps = try { requestJson(client, "$root/api/capabilities", token) }
                    catch (cancel: CancellationException) { throw cancel }
                    catch (error: Exception) { JSONObject().put("probeError", true) }
                    Triple(requestJson(client, "$root/api/status$threadQuery", token),
                        requestJson(client, "$root/api/history$threadQuery", token), caps)
                }
            }
            val (state, history, caps) = pair
            withContext(Dispatchers.Main) {
                if (requestedGeneration != historyGeneration || (requestedThreadId.isNotBlank() && requestedThreadId != selectedThreadId)) return@withContext
                val compatible = caps.optInt("protocolVersion") == 1
                workspaceAdvanced = compatible && caps.optJSONObject("features")?.optBoolean("workspacePaging") == true
                compatibilityNote = if (compatible && caps.optJSONObject("features")?.optBoolean("controls") == true) ""
                    else "Совместимость не подтверждена. Обновите агент или повторите проверку."
                if (selectedThreadId.isBlank()) {
                    selectedThreadId = state.optString("threadId")
                    prefs.edit().putString("selectedThreadId", selectedThreadId).apply()
                }
                title = state.optString("title", "Задача Codex")
                projectName = if (state.isNull("cwd")) "" else File(state.optString("cwd")).name
                threadModel = if (state.isNull("model")) "" else state.optString("model")
                threadEffort = if (state.isNull("reasoningEffort")) "" else state.optString("reasoningEffort")
                val busy = state.optString("status") == "active"
                codexBusy = busy
                activeTurnId = state.optString("activeTurnId")
                if (taskNotifications && activeTurnId.isNotBlank()) {
                    try { TaskWatchService.watch(context, selectedThreadId, activeTurnId) }
                    catch (failure: IllegalStateException) { status = "Фоновое наблюдение недоступно: откройте приложение" }
                }
                val queued = state.optInt("queuedCount", 0)
                val queueError = state.optString("queueError")
                val chatQueue = localQueue.filter { it.threadId == selectedThreadId }
                status = when {
                    chatQueue.isNotEmpty() && localQueueError.isNotBlank() ->
                        "Сообщение на телефоне: $localQueueError"
                    chatQueue.any { it.acceptedByBridge } -> "Сообщение в очереди Codex"
                    chatQueue.isNotEmpty() -> "На телефоне · ожидает отправки"
                    queued > 0 && queueError.isNotBlank() -> "Не удаётся доставить сообщение: $queueError"
                    queued > 0 && state.optInt("oldestQueuedSeconds") > 300 ->
                        "Сообщение в очереди более 5 минут · Codex занят"
                    queued > 0 -> "В очереди: $queued"
                    busy -> "Codex отвечает…"
                    else -> "Подключено"
                }
                val queueThread = selectedThreadId
                try {
                    val snapshot = withReachableHost { root -> withContext(Dispatchers.IO) {
                        requestJson(client, "$root/api/control?section=queue&threadId=${Uri.encode(queueThread)}", token)
                    } }
                    if (requestedGeneration != historyGeneration || queueThread != selectedThreadId) return@withContext
                    val cancelled = snapshot.optJSONArray("cancelledIds") ?: JSONArray()
                    val cancelledIds = (0 until cancelled.length()).mapTo(HashSet()) { cancelled.optString(it) }
                    val dismissed = snapshot.optJSONArray("dismissedIds") ?: JSONArray()
                    val dismissedIds = (0 until dismissed.length()).mapTo(HashSet()) { dismissed.optString(it) }
                    localQueue.removeAll { it.id in cancelledIds || it.id in dismissedIds }
                    val receipts = snapshot.optJSONObject("receipts") ?: JSONObject()
                    val tracked = (snapshot.optJSONArray("nativeTracking")?:JSONArray()).objects().associateBy { it.optString("id") }
                    for(index in localQueue.indices) tracked[localQueue[index].id]?.let { msg ->
                        localQueue[index]=localQueue[index].copy(text=msg.optString("text",localQueue[index].text), queueState=msg.optString("queueState","queued"))
                    }
                    for (index in localQueue.indices) {
                        receipts.optJSONObject(localQueue[index].id)?.let { receipt ->
                            localQueue[index] = localQueue[index].copy(deliveredTurnId = receipt.optString("turnId"))
                        }
                    }
                    desktopQueue = (snapshot.optJSONArray("data") ?: JSONArray()).objects().map { msg ->
                        val text = (msg.optJSONArray("input") ?: JSONArray()).objects().filter { it.optString("type") == "text" }.joinToString("\n") { it.optString("text") }
                        LocalMessage(msg.optString("clientUserMessageId").ifBlank { msg.optString("id") }, text, emptyList(),
                            threadId = queueThread, acceptedByBridge = true, nativeSubmissionId = msg.optString("id"))
                    }
                } catch (cancel: CancellationException) { throw cancel }
                catch (failure: Exception) {
                    if (requestedGeneration == historyGeneration && queueThread == selectedThreadId && desktopQueue.isNotEmpty()) status = "Очередь не обновлена: ${failure.message}"
                }
                if (requestedGeneration != historyGeneration || queueThread != selectedThreadId) return@withContext
                val latest = parseHistory(history)
                historyReady = true
                initialHistoryLoading = false
                initialHistoryError = ""
                val confirmed = latest.filter { it.role == "user" }
                localQueue.removeAll { pending ->
                    confirmed.any { line ->
                        line.clientMessageId == pending.id || (pending.deliveredTurnId.isNotBlank() &&
                        line.turnId == pending.deliveredTurnId &&
                            ((pending.text.isNotBlank() && line.text.trim() == pending.text.trim()) ||
                                (pending.text.isBlank() && pending.files.isNotEmpty() &&
                                    (line.images.isNotEmpty() || line.attachments.isNotEmpty()))))
                    }
                }
                saveLocalQueue(prefs, localQueue.toList())
                if (lines.isEmpty()) {
                    lines = latest
                    historyHasMore = history.optBoolean("hasMore")
                } else {
                    lines = mergeHistorySnapshot(lines, latest, history)
                    if (!olderPagesLoaded) historyHasMore = history.optBoolean("hasMore")
                }
            }
        } catch (cancel: CancellationException) {
            throw cancel
        } catch (error: Exception) {
            if (requestedThreadId == selectedThreadId && !historyReady) {
                initialHistoryLoading = false
                initialHistoryError = "Не удалось загрузить историю: ${error.message}"
            }
            throw error
        }
    }

    suspend fun browseWorkspace(path: String, clearPreview: Boolean = true, query: String = "",
        hidden: Boolean = workspacePrefs.getBoolean("hidden",false), service: Boolean = workspacePrefs.getBoolean("service",false), cursor: String = "") {
        val revision = ++workspaceRevision
        val thread = workspaceThreadId.ifBlank { selectedThreadId }
        if (workspaceThreadId.isBlank()) workspaceThreadId = thread
        val sameFolder = workspacePath == path
        workspacePath = path
        workspaceLoading = true
        workspaceError = ""
        if (cursor.isBlank() && !sameFolder) { workspaceEntries = emptyList(); workspaceNextCursor = "" }
        try {
            val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/workspace?threadId=${Uri.encode(thread)}&path=${Uri.encode(path)}&query=${Uri.encode(query)}&hidden=$hidden&service=$service&cursor=${Uri.encode(cursor)}", token)
            } }
            if (thread == workspaceThreadId && path == workspacePath && revision == workspaceRevision) {
                if (clearPreview) previewPath = ""
                workspaceRoot = result.optString("rootName")
                val entries = result.optJSONArray("entries") ?: JSONArray()
                val page = (0 until entries.length()).mapNotNull { index ->
                    val item = entries.optJSONObject(index) ?: return@mapNotNull null
                    WorkspaceEntry(item.optString("name"), item.optString("path"),
                        item.optBoolean("isDirectory"), item.optLong("size"),item.optBoolean("available",true),item.optString("blockedReason"),item.optBoolean("isLink"))
                }
                workspaceEntries = (if(cursor.isBlank()) page else workspaceEntries + page).distinctBy { it.path }
                workspaceNextCursor = result.optString("nextCursor").takeUnless { it=="null" }.orEmpty()
                workspaceTruncated = result.optBoolean("truncated")
            }
        } catch(cancel: CancellationException) { throw cancel }
        catch (error: Exception) {
            if (thread == workspaceThreadId && path == workspacePath && revision == workspaceRevision)
                workspaceError = "Не удалось открыть папку: ${error.message}"
        } finally {
            if (thread == workspaceThreadId && path == workspacePath && revision == workspaceRevision) workspaceLoading = false
        }
    }

    suspend fun resolveProjectFile(reference: String, sourceThread: String): String? {
        return try {
            val response = withReachableHost { root -> withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/workspace/resolve?threadId=${Uri.encode(sourceThread)}&path=${Uri.encode(reference)}", token)
            } }
            workspaceThreadId = sourceThread
            response.optString("folder")
        } catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { status = "Не удалось открыть файл проекта: ${failure.message}"; null }
    }

    suspend fun previewWorkspace(path: String) {
        if (path.isBlank()) { previewPath = ""; return }
        val thread = workspaceThreadId.ifBlank { selectedThreadId }
        previewPath = path
        previewText = ""
        previewNote = ""
        if (workspaceEntries.any { it.path == path && isSupportedImage(it.name) }) {
            previewLoading = false
            return
        }
        previewLoading = true
        try {
            val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/workspace/preview?threadId=${Uri.encode(thread)}&path=${Uri.encode(path)}", token)
            } }
            if (thread == workspaceThreadId && path == previewPath) {
                if (result.optBoolean("previewable")) {
                    previewText = result.optString("text")
                    if (result.optBoolean("truncated")) previewNote = "Показано начало файла. Полную версию можно сохранить."
                } else previewNote = result.optString("reason", "Предпросмотр недоступен")
            }
        } catch (error: Exception) {
            if (thread == workspaceThreadId && path == previewPath)
                previewNote = "Не удалось открыть файл: ${error.message}"
        } finally {
            if (thread == workspaceThreadId && path == previewPath) previewLoading = false
        }
    }

    suspend fun loadOlderHistory() {
        if (loadingOlder || !historyHasMore || lines.isEmpty() || token.isBlank()) return
        val threadId = selectedThreadId
        val before = lines.first().id
        if (before.isBlank()) return
        loadingOlder = true
        historyError = ""
        try {
            val page = withReachableHost { root ->
                withContext(Dispatchers.IO) {
                    requestJson(client, "$root/api/history?threadId=${Uri.encode(threadId)}&before=${Uri.encode(before)}", token)
                }
            }
            if (threadId == selectedThreadId) {
                val old = parseHistory(page)
                val combined = LinkedHashMap<String, ChatLine>()
                old.forEach { combined[it.id] = it }
                lines.forEach { combined[it.id] = it }
                lines = combined.values.toList()
                historyHasMore = page.optBoolean("hasMore")
                olderPagesLoaded = true
            }
        } catch (error: Exception) {
            historyError = "Не удалось загрузить историю: ${error.message}"
        } finally {
            loadingOlder = false
        }
    }

    suspend fun loadCatalog() {
        if (token.isBlank() || catalogLoading) return
        catalogLoading = true
        catalogError = ""
        try {
            val (projectData, modelData) = withReachableHost { root ->
                withContext(Dispatchers.IO) {
                    requestJson(client, "$root/api/projects", token) to
                        requestJson(client, "$root/api/models", token)
                }
            }
            val projectArray = projectData.optJSONArray("projects") ?: JSONArray()
            projects.clear()
            val seenThreadIds = mutableSetOf<String>()
            for (i in 0 until projectArray.length()) {
                val group = projectArray.optJSONObject(i) ?: continue
                val threadArray = group.optJSONArray("threads") ?: JSONArray()
                val threads = (0 until threadArray.length()).mapNotNull { j ->
                    val item = threadArray.optJSONObject(j) ?: return@mapNotNull null
                    val id = item.optString("id")
                    if (id.isBlank() || !seenThreadIds.add(id)) return@mapNotNull null
                    ThreadItem(id, item.optString("title", "Новый чат"), item.optString("status"),
                        item.optLong("updatedAt").let { if (it in 1..999_999_999_999L) it * 1000 else it })
                }
                if (threads.isNotEmpty() || group.optString("id") != "other") projects.add(ProjectGroup(group.optString("id"), group.optString("name"),
                    if (group.isNull("cwd")) null else group.optString("cwd").ifBlank { null }, threads))
            }
            val modelArray = modelData.optJSONArray("models") ?: JSONArray()
            models.clear()
            for (i in 0 until modelArray.length()) {
                val item = modelArray.optJSONObject(i) ?: continue
                val efforts = item.optJSONArray("efforts") ?: JSONArray()
                models.add(ModelOption(item.optString("id"), item.optString("name"),
                    (0 until efforts.length()).map { efforts.optString(it) },
                    if (item.isNull("defaultEffort")) "" else item.optString("defaultEffort")))
            }
        } catch (cancel: CancellationException) {
            throw cancel
        } catch (error: Exception) {
            catalogError = "Не удалось обновить проекты и чаты: ${error.message}"
            throw error
        } finally {
            catalogLoading = false
        }
    }

    suspend fun loadLimits() {
        if (token.isBlank() || limitsLoading) return
        limitsLoading = true
        limitsError = ""
        try {
            val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/limits", token)
            } }
            fun parseWindow(key: String): LimitWindow? = result.optJSONObject(key)?.let { item ->
                if (!item.has("remainingPercent") || item.isNull("remainingPercent")) null
                else LimitWindow(item.optInt("remainingPercent").coerceIn(0, 100),
                    item.optLong("resetsAt"))
            }
            usageLimits = UsageLimits(parseWindow("fiveHours"), parseWindow("week"),
                result.optLong("updatedAt"), if (result.isNull("resetCredits")) null else result.optInt("resetCredits"))
        } catch (error: Exception) {
            limitsError = "Не удалось обновить лимиты"
        } finally {
            limitsLoading = false
        }
    }

    suspend fun selectThread(threadId: String) {
        withReachableHost { root ->
            val body = JSONObject().put("threadId", threadId).toString()
                .toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$root/api/threads/select")
                .header("Authorization", "Bearer $token").post(body).build()
            withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error(JSONObject(response.body?.string().orEmpty()).optString("error"))
                }
            }
        }
        prefs.edit().putString("draft:$selectedThreadId", input).apply()
        historyGeneration++
        workspaceThreadId = threadId
        selectedThreadId = threadId
        desktopQueue = emptyList()
        activeTurnId = ""
        prefs.edit().putString("selectedThreadId", threadId).apply()
        input = prefs.getString("draft:$threadId", "") ?: ""
        selectedModel = ""
        selectedEffort = ""
        threadModel = ""
        threadEffort = ""
        lines = emptyList()
        historyHasMore = false
        olderPagesLoaded = false
        historyError = ""
        historyReady = false
        initialHistoryLoading = token.isNotBlank()
        initialHistoryError = ""
        refresh()
    }

    suspend fun createThread(cwd: String?) {
        val result = withReachableHost { root ->
            val body = JSONObject().apply { if (!cwd.isNullOrBlank()) put("cwd", cwd) }
                .toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$root/api/threads")
                .header("Authorization", "Bearer $token").post(body).build()
            withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    val json = JSONObject(response.body?.string().orEmpty())
                    if (!response.isSuccessful) error(json.optString("error", "Не удалось создать чат"))
                    json
                }
            }
        }
        val id = result.getString("threadId")
        prefs.edit().putString("draft:$selectedThreadId", input).apply()
        historyGeneration++
        workspaceThreadId = id
        selectedThreadId = id
        prefs.edit().putString("selectedThreadId", id).apply()
        input = ""
        selectedModel = ""
        selectedEffort = ""
        threadModel = ""
        threadEffort = ""
        lines = emptyList()
        historyHasMore = false
        olderPagesLoaded = false
        historyError = ""
        historyReady = false
        initialHistoryLoading = token.isNotBlank()
        initialHistoryError = ""
        refresh()
        loadCatalog()
    }

    suspend fun deleteThread(threadId: String) {
        val result = withReachableHost { root ->
            val body = JSONObject().put("threadId", threadId).toString()
                .toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$root/api/threads/delete")
                .header("Authorization", "Bearer $token").post(body).build()
            withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    val json = JSONObject(response.body?.string().orEmpty())
                    if (!response.isSuccessful) error(json.optString("error", "Не удалось удалить чат"))
                    json
                }
            }
        }
        prefs.edit().remove("draft:$threadId").apply()
        val nextId = result.optString("selectedThreadId")
        if (selectedThreadId == threadId && nextId.isNotBlank()) {
            historyGeneration++
            workspaceThreadId = nextId
            selectedThreadId = nextId
            prefs.edit().putString("selectedThreadId", nextId).apply()
            input = prefs.getString("draft:$nextId", "") ?: ""
            selectedModel = ""
            selectedEffort = ""
            threadModel = ""
            threadEffort = ""
            lines = emptyList()
            historyHasMore = false
            olderPagesLoaded = false
            historyError = ""
            historyReady = false
            initialHistoryLoading = token.isNotBlank()
            initialHistoryError = ""
            refresh()
        }
        loadCatalog()
    }

    suspend fun moveThread(threadId: String, projectId: String) {
        withReachableHost { root ->
            val body = JSONObject().put("threadId", threadId).put("projectId", projectId)
                .toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder().url("$root/api/threads/project")
                .header("Authorization", "Bearer $token").post(body).build()
            withContext(Dispatchers.IO) {
                client.newCall(request).execute().use { response ->
                    val json = JSONObject(response.body?.string().orEmpty())
                    if (!response.isSuccessful) error(json.optString("error", "Не удалось переместить чат"))
                }
            }
        }
        loadCatalog()
    }

    suspend fun acceptPair(uri: Uri) {
        status = "Подключаюсь…"
        val result = pairFromCode(uri)
        host = result.host
        tailHost = result.tailHost
        activeHost = result.activeHost
        token = result.token
        certificatePin = result.fingerprint
        historyGeneration++
        workspaceThreadId = result.threadId
        selectedThreadId = result.threadId
        prefs.edit().putString("selectedThreadId", selectedThreadId).apply()
        selectedModel = ""
        selectedEffort = ""
        threadModel = ""
        threadEffort = ""
        lines = emptyList()
        historyHasMore = false
        olderPagesLoaded = false
        historyError = ""
        historyReady = false
        initialHistoryLoading = token.isNotBlank()
        initialHistoryError = ""
        saveToken(context, prefs, token)
        prefs.edit().putString("host", host).putString("tailHost", tailHost)
            .putString("certificatePin", certificatePin).apply()
        status = "Подключено"
    }

    suspend fun cancelQueued(messageId: String, dismissUnknown: Boolean = false) {
        val body = JSONObject().put("clientMessageId", messageId).put("dismissUnknown", dismissUnknown)
            .put("confirmed", dismissUnknown).toString()
            .toRequestBody("application/json".toMediaType())
        val result = withReachableHost { root -> withContext(Dispatchers.IO) {
            val request = Request.Builder().url("$root/api/messages/cancel")
                .header("Authorization", "Bearer $token").post(body).build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) error("Отмена сообщения не удалась")
                JSONObject(response.body?.string().orEmpty())
            }
        } }
        localQueue.removeAll { it.id == messageId }
        saveLocalQueue(prefs, localQueue.toList())
        localQueueError = ""
        status = if (result.optString("status") == "sent") "Сообщение уже отправлено"
            else if (result.optString("status") == "dismissed") "Уведомление скрыто. Исход задачи в Desktop не подтверждён"
            else "Сообщение убрано из очереди"
    }

    suspend fun deliverPending() {
        if (token.isBlank() || delivering || localQueue.none { it.deliveredTurnId.isBlank() }) return
        delivering = true
        try {
            for (message in localQueue.toList()) {
                if (message.deliveredTurnId.isNotBlank()) continue
                if(message.acceptedByBridge && !message.cancelRequested) continue
                if (message.cancelRequested) {
                    cancelQueued(message.id)
                    continue
                }
                val payload = JSONObject().put("text", message.text)
                    .put("files", JSONArray(message.files))
                    .put("clientMessageId", message.id)
                    .put("threadId", message.threadId.ifBlank { selectedThreadId })
                    .apply {
                        message.model?.let { put("model", it) }
                        message.effort?.let { put("effort", it) }
                    }
                    .toString()
                val body = payload.toRequestBody("application/json".toMediaType())
                val result = withReachableHost { root ->
                    val request = Request.Builder().url("$root/api/send")
                        .header("Authorization", "Bearer $token").post(body).build()
                    withContext(Dispatchers.IO) {
                        client.newCall(request).execute().use { response ->
                            val json = JSONObject(response.body?.string().orEmpty())
                            if (!response.isSuccessful) error(json.optString("error", "Сообщение не принято"))
                            json
                        }
                    }
                }
                val actualThreadId = result.optString("threadId")
                if (actualThreadId.isNotBlank() && actualThreadId != message.threadId) {
                    if (selectedThreadId == message.threadId) {
                        historyGeneration++
                        workspaceThreadId = actualThreadId
                        selectedThreadId = actualThreadId
                        prefs.edit().putString("selectedThreadId", actualThreadId).apply()
                    }
                    for (index in localQueue.indices) {
                        if (localQueue[index].threadId == message.threadId) {
                            localQueue[index] = localQueue[index].copy(threadId = actualThreadId)
                        }
                    }
                    loadCatalog()
                }
                val startedTurn = result.optString("turnId")
                if (taskNotifications && startedTurn.isNotBlank()) {
                    try { TaskWatchService.watch(context, actualThreadId.ifBlank { message.threadId.ifBlank { selectedThreadId } }, startedTurn) }
                    catch (failure: IllegalStateException) { status = "Откройте приложение для фоновых уведомлений" }
                }
                if (result.optBoolean("queued")) {
                    val index = localQueue.indexOfFirst { it.id == message.id }
                    if (index >= 0) localQueue[index] = localQueue[index].copy(acceptedByBridge = true, nativeSubmissionId = result.optString("nativeSubmissionId"))
                } else {
                    val index = localQueue.indexOfFirst { it.id == message.id }
                    if (index >= 0) localQueue[index] = localQueue[index].copy(
                        deliveredTurnId = result.optString("turnId"))
                }
                saveLocalQueue(prefs, localQueue.toList())
                localQueueError = ""
                status = if (result.optBoolean("queued")) "Принято мостом · Codex занят" else "Принято Codex"
            }
        } finally {
            delivering = false
        }
    }

    fun disconnectDevice() {
        scope.launch {
            runCatching {
                withReachableHost { root ->
                    val request = Request.Builder().url("$root/api/unpair")
                        .header("Authorization", "Bearer $token")
                        .post(ByteArray(0).toRequestBody(null)).build()
                    withContext(Dispatchers.IO) {
                        client.newCall(request).execute().use { response ->
                            if (!response.isSuccessful) error("ПК не подтвердил отключение")
                        }
                    }
                }
            }.onSuccess {
                context.stopService(Intent(context, TaskWatchService::class.java))
                token = ""
                lines = emptyList()
                historyReady = false
                initialHistoryLoading = false
                initialHistoryError = ""
                catalogError = ""
                projects.clear()
                models.clear()
                usageLimits = null
                files.clear()
                savePendingFiles(prefs, emptyList())
                pendingFileDeletes.clear()
                savePendingDeletes(prefs, emptyList())
                clearSavedToken(context, prefs)
                resetRetryKey = null
                resetMessage = ""
                prefs.edit().remove("resetRetryKey").commit()
                status = "Телефон отключён"
            }.onFailure { status = "Не удалось отключить телефон: ${it.message}" }
        }
    }

    LaunchedEffect(notificationThreadId, notificationNonce, token) {
        if (notificationThreadId.isNotBlank() && token.isNotBlank()) {
            try { selectThread(notificationThreadId) }
            catch (cancel: CancellationException) { throw cancel }
            catch (failure: Exception) { status = "Не удалось открыть уведомление: ${failure.message}" }
        }
    }
    LaunchedEffect(externalPairingUri) {
        if (externalPairingUri != null) {
            runCatching { acceptPair(externalPairingUri) }
                .onFailure { status = "Не удалось подключиться: ${it.message}" }
        }
    }

    LaunchedEffect(token, host, tailHost) {
        if (token.isNotBlank()) {
            runCatching { loadCatalog() }
            // Restore transient workspace content after Activity recreation.
            val restoredPreview = previewPath
            val restoredFolder = workspacePath
            runCatching {
                browseWorkspace(restoredFolder, clearPreview = false)
                if (restoredPreview.isNotBlank()) previewWorkspace(restoredPreview)
            }
            while (true) {
                runCatching { cleanupPendingFiles() }
                runCatching { deliverPending() }
                    .onFailure {
                        localQueueError = it.message ?: "жду связи с ПК"
                        status = "Сообщение на телефоне: $localQueueError"
                    }
                runCatching { refresh() }.onSuccess { connectionState = ConnectionState.Online }.onFailure { error ->
                    connectionState = ConnectionState.Reconnecting
                    if (error.message?.contains("Требуется сопряжение") == true) {
                        clearSavedToken(context, prefs)
                        token = ""
                        status = "Привязка устарела · подключи телефон один раз"
                    } else {
                        status = "Нет связи с ПК · повторяю подключение"
                    }
                }
                delay(if (codexBusy) 2000 else 10000)
            }
        }
    }
    LaunchedEffect(token, host, tailHost) {
        if (token.isNotBlank()) {
            var cursor = ""
            while (true) {
                runCatching {
                    val event = withReachableHost { root ->
                        withContext(Dispatchers.IO) {
                            requestJson(client, "$root/api/events?cursor=${Uri.encode(cursor)}", token)
                        }
                    }
                    val next = event.optString("cursor")
                    if (next.isNotBlank() && next != cursor) {
                        cursor = next
                        refresh()
                        delay(500)
                    }
                }.onFailure { delay(2000) }
            }
        }
    }
    val darkTheme = when (themeMode) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    SideEffect {
        (context as? Activity)?.window?.let { window ->
            WindowCompat.getInsetsController(window, window.decorView).apply {
                isAppearanceLightStatusBars = !darkTheme
                isAppearanceLightNavigationBars = !darkTheme
            }
        }
    }
    androidx.compose.runtime.CompositionLocalProvider(LocalImageScope provides "$host:$certificatePin:$workspaceRevision") {
    MaterialTheme(colorScheme = if (darkTheme) darkPalette else lightPalette, typography = appTypography) {
        if (updatesOpen) UpdatesScreen(updates, onClose = { updatesOpen = false })
        else CompanionUi(
            activeTurnId = activeTurnId,
            taskNotifications = taskNotifications,
            onTaskNotifications = {
                if (taskNotifications) {
                    taskNotifications = false
                    prefs.edit().putBoolean("taskNotifications", false).apply()
                    TaskWatchService.clearSaved(context)
                    context.stopService(Intent(context, TaskWatchService::class.java))
                } else if (android.os.Build.VERSION.SDK_INT >= 33 && !TaskWatchService.canNotify(context)) {
                    notificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    taskNotifications = true
                    prefs.edit().putBoolean("taskNotifications", true).apply()
                }
            },
            onUpdates = { updatesOpen = true },
            updateAvailable = updates.update != null,
            themeMode = themeMode,
            onThemeMode = { value ->
                themeMode = value
                prefs.edit().putString("themeMode", value).apply()
            },
            paired = token.isNotBlank(), relayOnly = BuildConfig.RELAY_ONLY,
            title = title, projectName = projectName,
            status = status, connectionState = connectionState, usageLimits = usageLimits, limitsLoading = limitsLoading,
            limitsError = limitsError, lines = lines, queue = (desktopQueue + localQueue.toList()).distinctBy { it.id },
            selectedThreadId = selectedThreadId, projects = projects.toList(),
            catalogLoading = catalogLoading, catalogError = catalogError,
            models = models.toList(), selectedModel = selectedModel.ifBlank { threadModel },
            selectedEffort = selectedEffort.ifBlank { threadEffort },
            modelOverridden = selectedModel.isNotBlank(), effortOverridden = selectedEffort.isNotBlank(),
            input = input,
            attachments = files.toList(), outboxFiles = outboxFiles.toList(),
            outboxLoading = outboxLoading, outboxError = outboxError,
            workspaceRoot = workspaceRoot, workspacePath = workspacePath,
            workspaceEntries = workspaceEntries, workspaceLoading = workspaceLoading,
            workspaceError = workspaceError, workspaceTruncated = workspaceTruncated,
            previewPath = previewPath, previewText = previewText,
            previewNote = previewNote, previewLoading = previewLoading,
            fileStatus = fileStatus,
            transferringFileId = transferringFileId, transferProgress = transferProgress,
            historyHasMore = historyHasMore, historyLoading = loadingOlder,
            historyError = historyError,
            initialHistoryLoading = initialHistoryLoading, initialHistoryError = initialHistoryError,
            onJumpHistory = { target -> scope.launch {
                val requestedThread = selectedThreadId
                try {
                    val page = withReachableHost { root -> withContext(Dispatchers.IO) {
                        requestJson(client, "$root/api/history?threadId=${Uri.encode(requestedThread)}&around=${Uri.encode(target)}", token)
                    } }
                    if (requestedThread == selectedThreadId) {
                        lines = parseHistory(page); olderPagesLoaded = true
                        historyHasMore = page.optBoolean("hasMore"); historyError = ""
                    }
                } catch (cancel: CancellationException) { throw cancel }
                catch (failure: Exception) { historyError = "Не удалось открыть сообщение: ${failure.message}" }
            } },
            onRetryHistory = { scope.launch { runCatching { refresh() } } },
            onInput = {
                input = it
                prefs.edit().putString("draft:$selectedThreadId", it).apply()
            },
            onScan = {
                GmsBarcodeScanning.getClient(context).startScan()
                    .addOnSuccessListener { barcode ->
                        scope.launch {
                            runCatching { acceptPair(Uri.parse(barcode.rawValue ?: error("QR-код пуст"))) }
                                .onFailure { status = "Не удалось подключиться: " + it.message }
                        }
                    }
                    .addOnFailureListener { status = "Сканер недоступен: " + it.message }
            },
            onSelectThread = { id ->
                scope.launch {
                    runCatching { selectThread(id) }
                        .onFailure { status = "Не удалось открыть чат: ${it.message}" }
                }
            },
            onDeleteThread = { id ->
                scope.launch {
                    runCatching { deleteThread(id) }
                        .onFailure { status = "Не удалось удалить чат: ${it.message}" }
                }
            },
            onMoveThread = { id, projectId ->
                scope.launch {
                    runCatching { moveThread(id, projectId) }
                        .onFailure { status = "Не удалось переместить чат: ${it.message}" }
                }
            },
            onNewChat = { cwd ->
                scope.launch {
                    runCatching { createThread(cwd) }
                        .onFailure { status = "Не удалось создать чат: ${it.message}" }
                }
            },
            onRefreshCatalog = {
                scope.launch {
                    runCatching { loadCatalog() }
                        .onFailure { status = "Список чатов недоступен: ${it.message}" }
                }
            },
            onRefreshLimits = { scope.launch { loadLimits() } },
            resetMessage = resetMessage, resetLoading = resetLoading, resetPending = resetRetryKey != null,
            onResetLimits = { attempt ->
                if (!resetLoading) scope.launch {
                    resetLoading = true
                    val key = resetRetryKey ?: attempt
                    resetRetryKey = key
                    prefs.edit().putString("resetRetryKey", key).commit()
                    try {
                        val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                            val body = JSONObject().put("idempotencyKey", key).toString()
                                .toRequestBody("application/json".toMediaType())
                            client.newCall(Request.Builder().url("$root/api/limits/reset")
                                .header("Authorization", "Bearer $token").post(body).build()).execute().use { response ->
                                val json = JSONObject(response.body?.string().orEmpty())
                                if (!response.isSuccessful) error(json.optString("error", "Сброс не выполнен"))
                                json
                            }
                        } }
                        resetMessage = when (result.optString("outcome")) {
                            "reset", "alreadyRedeemed" -> "Лимиты сброшены"
                            "nothingToReset" -> "Сейчас нет лимитов, доступных для сброса"
                            "noCredit" -> "Нет доступных кредитов сброса"
                            else -> "Сервер не подтвердил сброс"
                        }
                        if (result.optString("outcome") in listOf("reset", "alreadyRedeemed", "nothingToReset", "noCredit")) {
                            resetRetryKey = null
                            prefs.edit().remove("resetRetryKey").commit()
                        }
                        loadLimits()
                    } catch (error: Exception) {
                        resetMessage = "Результат сброса не подтверждён: ${error.message}. Повтор использует тот же запрос."
                    } finally { resetLoading = false }
                }
            },
            onSubagentRequest = { path, payload ->
                withReachableHost { root -> withContext(Dispatchers.IO) {
                    val builder = Request.Builder().url("$root/api/$path")
                        .header("Authorization", "Bearer $token")
                    if (payload != null) builder.post(payload.toString()
                        .toRequestBody("application/json".toMediaType()))
                    client.newCall(builder.build()).execute().use { response ->
                        val result = JSONObject(response.body?.string().orEmpty())
                        if (!response.isSuccessful) error(result.optString("error", "Ошибка запроса"))
                        result
                    }
                } }
            },
            onAdbRequest = { path, payload ->
                withReachableHost { root -> withContext(Dispatchers.IO) {
                    val builder = Request.Builder().url("$root/api/adb/$path")
                        .header("Authorization", "Bearer $token")
                    if (payload != null) builder.post(payload.toString()
                        .toRequestBody("application/json".toMediaType()))
                    client.newCall(builder.build()).execute().use { response ->
                        val result = JSONObject(response.body?.string().orEmpty())
                        if (!response.isSuccessful) error(result.optString("error", "Ошибка ADB"))
                        result
                    }
                } }
            },
            onModel = { id ->
                selectedModel = id
                val option = models.firstOrNull { it.id == id }
                if (option != null && selectedEffort !in option.efforts) selectedEffort = option.defaultEffort
            },
            onEffort = { selectedEffort = it },
            onAttach = { picker.launch(arrayOf("*/*")) },
            onRemoveAttachment = { file ->
                files.remove(file)
                savePendingFiles(prefs, files.toList())
                if (file.id !in pendingFileDeletes) pendingFileDeletes.add(file.id)
                savePendingDeletes(prefs, pendingFileDeletes.toList())
                scope.launch { runCatching { cleanupPendingFiles() } }
            },
            onFetchFiles = {
                outboxLoading = true
                outboxError = ""
                outboxFiles.clear()
                scope.launch {
                    runCatching {
                        withReachableHost { root ->
                            withContext(Dispatchers.IO) { requestJson(client, "$root/api/outbox", token) }
                        }
                    }.onSuccess { response ->
                        outboxFiles.clear()
                        val entries = response.optJSONArray("files") ?: JSONArray()
                        for (i in 0 until entries.length()) {
                            val item = entries.optJSONObject(i) ?: continue
                            outboxFiles.add(RemoteFile(item.optString("id"), item.optString("name"), item.optLong("size")))
                        }
                    }.onFailure {
                        outboxError = "Список файлов недоступен: ${it.message}"
                        status = outboxError
                    }
                    outboxLoading = false
                }
            },
            onSaveFile = { file ->
                selectedOutbox = file
                fileStatus = ""
                savePicker.launch(file.name)
            },
            workspaceNextCursor = workspaceNextCursor,
            compatibilityNote = compatibilityNote,
            onWorkspaceQuery = if(workspaceAdvanced) { path, query, hidden, service, cursor -> scope.launch { browseWorkspace(path, false, query, hidden, service, cursor) }; Unit } else null,
            onBrowseWorkspace = { path -> scope.launch { browseWorkspace(path) } },
            onPreviewWorkspace = { path -> scope.launch { previewWorkspace(path) } },
            onAskWorkspace = { path ->
                val sourceThread = workspaceThreadId
                val targetThread = selectedThreadId
                scope.launch {
                    try {
                        val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                            requestJson(client, "$root/api/workspace/resolve?threadId=${Uri.encode(sourceThread)}&path=${Uri.encode(path)}", token)
                        } }
                        if (selectedThreadId == targetThread) {
                            val absolute = result.optString("absolutePath")
                            if (absolute.isBlank() && sourceThread != targetThread) error("Обновите агент для передачи пути файла из другого чата")
                            val prompt = "Посмотри файл `${absolute.ifBlank { path }}`. "
                            input = if (input.isBlank()) prompt else "$input\n$prompt"
                            prefs.edit().putString("draft:$selectedThreadId", input).apply()
                        }
                    } catch (cancel: CancellationException) { throw cancel }
                    catch (error: Exception) { workspaceError = "Не удалось добавить файл: ${error.message}" }
                }
            },
            onSaveWorkspace = { entry ->
                selectedOutbox = RemoteFile("", entry.name, entry.size, entry.path, workspaceThreadId)
                fileStatus = ""
                savePicker.launch(entry.name)
            },
            onResolveProjectFile = { reference -> resolveProjectFile(reference, selectedThreadId) },
            onResolveContextFile = { reference, source -> resolveProjectFile(reference, source) },
            workspaceThreadId = workspaceThreadId,
            onSaveChatImage = { image ->
                selectedChatImage = image
                saveChatImagePicker.launch(image.name.ifBlank { "image.png" })
            },
            onLoadOlder = { scope.launch { loadOlderHistory() } },
            loadImage = { endpoint -> fetchImage(endpoint) },
            onSend = {
                val pending = LocalMessage(UUID.randomUUID().toString(), input,
                    files.map { it.id }, selectedThreadId,
                    selectedModel.ifBlank { null }, selectedEffort.ifBlank { null })
                localQueue.add(pending)
                saveLocalQueue(prefs, localQueue.toList())
                input = ""
                prefs.edit().remove("draft:$selectedThreadId").apply()
                files.clear()
                savePendingFiles(prefs, emptyList())
                localQueueError = ""
                status = "На телефоне · ожидает отправки"
                scope.launch {
                    runCatching { deliverPending() }
                        .onFailure {
                            localQueueError = it.message ?: "жду связи с ПК"
                            status = "Сообщение на телефоне: $localQueueError"
                        }
                    runCatching { refresh() }
                }
            },
            onCancelQueued = { message ->
                if (message.queueState == "checking") {
                    scope.launch { runCatching { cancelQueued(message.id, dismissUnknown = true) }
                        .onFailure { status = "Не удалось скрыть: ${it.message}" } }
                } else if (message.nativeSubmissionId.isNotBlank()) {
                    scope.launch {
                        try {
                            val payload = JSONObject().put("threadId", message.threadId).put("action", "queue-delete").put("submissionId", message.nativeSubmissionId)
                            withReachableHost { root -> withContext(Dispatchers.IO) {
                                val request = Request.Builder().url("$root/api/control/action").header("Authorization", "Bearer $token")
                                    .post(payload.toString().toRequestBody("application/json".toMediaType())).build()
                                client.newCall(request).execute().use { res -> if (!res.isSuccessful) error(JSONObject(res.body?.string().orEmpty()).optString("error")) }
                            } }
                            desktopQueue = desktopQueue.filterNot { it.id == message.id }
                            localQueue.removeAll { it.id == message.id }; saveLocalQueue(prefs, localQueue.toList())
                        } catch (cancel: CancellationException) { throw cancel }
                        catch (failure: Exception) { status = "Не удалось отменить: ${failure.message}" }
                    }
                }
                val index = localQueue.indexOfFirst { it.id == message.id }
                if (index >= 0 && message.nativeSubmissionId.isBlank() && message.queueState != "checking") {
                    localQueue[index] = localQueue[index].copy(cancelRequested = true)
                    saveLocalQueue(prefs, localQueue.toList())
                    scope.launch { runCatching { cancelQueued(message.id) }
                        .onFailure { status = "Отмена ждёт связи с ПК" } }
                }
            },
            onSteerQueued = { message ->
                if (message.acceptedByBridge && !message.cancelRequested) {
                    scope.launch {
                        val body = JSONObject().put("clientMessageId", message.id).toString()
                            .toRequestBody("application/json".toMediaType())
                        runCatching {
                            withReachableHost { root -> withContext(Dispatchers.IO) {
                                val request = Request.Builder().url("$root/api/messages/steer")
                                    .header("Authorization", "Bearer $token").post(body).build()
                                client.newCall(request).execute().use { response ->
                                    val result = JSONObject(response.body?.string().orEmpty())
                                    if (!response.isSuccessful)
                                        error(result.optString("error", "Корректировка не принята"))
                                    result
                                }
                            } }
                        }.onSuccess { result ->
                            val index = localQueue.indexOfFirst { it.id == message.id }
                            if (index >= 0) localQueue[index] = localQueue[index].copy(
                                deliveredTurnId = result.optString("turnId"),
                                steered = result.optBoolean("steered"))
                            saveLocalQueue(prefs, localQueue.toList())
                            localQueueError = ""
                            status = if (result.optBoolean("steered"))
                                "Корректировка отправлена в текущий ход"
                            else "Сообщение уже отправлено отдельным ходом"
                            runCatching { refresh() }
                        }.onFailure {
                            status = "Корректировка не отправлена: ${it.message}"
                        }
                    }
                }
            },
            onCancelTransfer = {
                activeTransferCall.get()?.cancel()
                activeUploadJob?.cancel()
                activeDownloadJob?.cancel()
            },
            onDisconnect = { disconnectDevice() },
        )
    }
    }

}

private suspend fun uploadFile(context: Context, client: OkHttpClient, host: String, token: String,
                               uri: Uri, uploadId: String, activeCall: AtomicReference<Call?>): PendingFile = withContext(Dispatchers.IO) {
    val name = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) cursor.getString(0) else null
    } ?: "file"
    val mime = context.contentResolver.getType(uri) ?: "application/octet-stream"
    val temporary = File.createTempFile("codex-upload-", ".tmp", context.cacheDir)
    try {
        val digest = MessageDigest.getInstance("SHA-256")
        context.contentResolver.openInputStream(uri)?.use { input ->
            temporary.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    currentCoroutineContext().ensureActive()
                    val count = input.read(buffer)
                    if (count < 0) break
                    total += count
                    require(total <= 40L * 1024 * 1024) { "Максимальный размер файла — 40 МБ" }
                    digest.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                }
            }
        } ?: error("Не удалось прочитать файл")
    val encodedName = Base64.encodeToString(name.toByteArray(Charsets.UTF_8), Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    val request = Request.Builder()
        .url(host.trimEnd('/') + "/api/upload")
        .header("Authorization", "Bearer $token")
        .header("X-Filename-Base64", encodedName)
        .header("X-Content-SHA256", digest.digest().joinToString("") { "%02x".format(it) })
        .header("X-Upload-ID", uploadId)
        .header("Content-Type", mime)
        .post(temporary.asRequestBody(mime.toMediaType()))
        .build()
    val call = client.newCall(request)
    activeCall.set(call)
    val cancellation = currentCoroutineContext().job.invokeOnCompletion { if (it != null) call.cancel() }
    try { call.execute().use { response ->
        val json = JSONObject(response.body?.string().orEmpty())
        if (!response.isSuccessful) error(json.optString("error", "Файл не принят"))
        PendingFile(json.getString("id"), json.optString("name", name))
    } } finally { cancellation.dispose(); activeCall.compareAndSet(call, null) }
    } finally {
        temporary.delete()
    }
}

internal suspend fun downloadFile(
    context: Context, client: OkHttpClient, host: String, token: String,
    file: RemoteFile, destination: Uri, activeCall: AtomicReference<Call?>,
    onProgress: suspend (Long, Long) -> Unit
) = withContext(Dispatchers.IO) {
    val endpoint = if (file.workspacePath.isNotBlank())
        "/api/workspace/download?threadId=${Uri.encode(file.threadId)}&path=${Uri.encode(file.workspacePath)}"
    else "/api/download?id=${Uri.encode(file.id)}"
    val request = Request.Builder().url(host.trimEnd('/') + endpoint)
        .header("Authorization", "Bearer $token").get().build()
    val call = client.newCall(request)
    activeCall.set(call)
    val cancellation = currentCoroutineContext().job.invokeOnCompletion { if (it != null) call.cancel() }
    val staged = File.createTempFile("verified-download-", ".part", context.cacheDir)
    try { call.execute().use { response ->
            if (!response.isSuccessful) error("ПК вернул ошибку ${response.code}")
            val expected = response.header("X-Content-SHA256") ?: error("Нет контрольной суммы")
            val digest = MessageDigest.getInstance("SHA-256")
            val stream = response.body?.byteStream() ?: error("Файл пуст")
            val total = response.body?.contentLength()?.takeIf { it > 0 } ?: file.size
            var written = 0L
            staged.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = stream.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                    written += count
                    withContext(Dispatchers.Main) { onProgress(written, total) }
                }
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            check(actual.equals(expected, ignoreCase = true)) { "Контрольная сумма не совпала" }
            currentCoroutineContext().ensureActive()
            context.contentResolver.openOutputStream(destination, "rwt")?.use { output ->
                staged.inputStream().use { input -> input.copyTo(output) }
            } ?: error("Не удалось записать файл")
    } } finally { staged.delete(); cancellation.dispose(); activeCall.compareAndSet(call, null) }
}

internal fun requestJson(client: OkHttpClient, url: String, token: String): JSONObject {
    val request = Request.Builder().url(url).header("Authorization", "Bearer $token").get().build()
    return client.newCall(request).execute().use { response ->
        val json = JSONObject(response.body?.string().orEmpty())
        if (!response.isSuccessful) error(json.optString("error", "Запрос не удался"))
        json
    }
}

internal fun pinnedClient(fingerprintInput: String): OkHttpClient {
    val expected = fingerprintInput.filter { it.isLetterOrDigit() }.uppercase()
    val trustManager = object : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            if (expected.length != 64 || chain.isNullOrEmpty()) throw CertificateException("Нет доверенного сертификата ПК")
            val cert = chain[0]
            cert.checkValidity()
            val actual = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
                .joinToString("") { byte -> "%02X".format(byte) }
            if (!MessageDigest.isEqual(actual.toByteArray(), expected.toByteArray())) {
                throw CertificateException("Отпечаток сертификата ПК не совпадает")
            }
        }
    }
    val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trustManager), SecureRandom()) }
    return OkHttpClient.Builder()
        .sslSocketFactory(ssl.socketFactory, trustManager)
        // TLS identity is verified against the exact fingerprint entered by the user.
        .hostnameVerifier { _, _ -> true }
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
}

private const val TOKEN_KEY_ALIAS = "codex-phone-bridge-token-v1"

private fun tokenKey(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey(TOKEN_KEY_ALIAS, null) as? SecretKey)?.let { return it }
    return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
        init(
            KeyGenParameterSpec.Builder(
                TOKEN_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        generateKey()
    }
}

internal fun saveToken(context: Context, prefs: android.content.SharedPreferences, token: String) {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, tokenKey()) }
    val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
    prefs.edit()
        .putString("tokenIv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
        .putString("tokenCiphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        .apply()
}

internal fun readSavedToken(context: Context, prefs: android.content.SharedPreferences): String {
    val ivText = prefs.getString("tokenIv", null) ?: return ""
    val cipherText = prefs.getString("tokenCiphertext", null) ?: return ""
    return runCatching {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, tokenKey(), javax.crypto.spec.GCMParameterSpec(128, Base64.decode(ivText, Base64.NO_WRAP)))
        String(cipher.doFinal(Base64.decode(cipherText, Base64.NO_WRAP)), Charsets.UTF_8)
    }.getOrElse {
        clearSavedToken(context, prefs)
        ""
    }
}

private fun clearSavedToken(context: Context, prefs: android.content.SharedPreferences) {
    prefs.edit().remove("tokenIv").remove("tokenCiphertext").apply()
}
