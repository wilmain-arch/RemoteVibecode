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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
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
            item.optBoolean("acceptedByBridge"), item.optBoolean("cancelRequested"))
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
            .put("cancelRequested", message.cancelRequested))
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
    val pin = uri.getQueryParameter("pin") ?: error("Нет кода привязки")
    val fingerprint = (uri.getQueryParameter("fingerprint") ?: "")
        .filter { it.isLetterOrDigit() }.uppercase()
    require(host.startsWith("https://") && fingerprint.length == 64) { "Недействительный код привязки" }
    val client = pinnedClient(fingerprint)
    val body = JSONObject().put("pin", pin).toString().toRequestBody("application/json".toMediaType())
    var lastError: Throwable? = null
    for (candidate in listOf(host, tailHost).filter { it.isNotBlank() }) {
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
private val lightPalette = lightColorScheme(
    primary = Color(0xFF202321),
    onPrimary = Color.White,
    secondary = Color(0xFF247661),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE5F1EA),
    onSecondaryContainer = Color(0xFF17634F),
    background = Color(0xFFF8F9F7),
    onBackground = Color(0xFF202321),
    surface = Color(0xFFF8F9F7),
    onSurface = Color(0xFF202321),
    surfaceVariant = Color(0xFFEBEEEA),
    onSurfaceVariant = Color(0xFF727774),
    outlineVariant = Color(0xFFE4E7E3),
)
private val darkPalette = darkColorScheme(
    primary = Color(0xFFF2F4F1),
    onPrimary = Color(0xFF141715),
    secondary = Color(0xFF8CCDB4),
    onSecondary = Color(0xFF141715),
    secondaryContainer = Color(0xFF20382E),
    onSecondaryContainer = Color(0xFF8CCDB4),
    background = Color(0xFF141715),
    onBackground = Color(0xFFF2F4F1),
    surface = Color(0xFF141715),
    onSurface = Color(0xFFF2F4F1),
    surfaceVariant = Color(0xFF242925),
    onSurfaceVariant = Color(0xFFA0A8A2),
    outlineVariant = Color(0xFF303632),
)

class MainActivity : ComponentActivity() {
    private var pairingUri by mutableStateOf<Uri?>(null)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.isNavigationBarContrastEnforced = false
        pairingUri = intent?.data
        setContent { CompanionScreen(pairingUri) }
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairingUri = intent.data
    }
}

@Composable
private fun CompanionScreen(externalPairingUri: Uri?) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("companion", Context.MODE_PRIVATE) }
    var themeMode by remember { mutableStateOf(prefs.getString("themeMode", "system") ?: "system") }
    val scope = rememberCoroutineScope()
    var certificatePin by remember { mutableStateOf(prefs.getString("certificatePin", "") ?: "") }
    val client = remember(certificatePin) { pinnedClient(certificatePin) }
    var host by remember { mutableStateOf(prefs.getString("host", "") ?: "") }
    var tailHost by remember { mutableStateOf(prefs.getString("tailHost", "") ?: "") }
    var activeHost by remember { mutableStateOf(host) }
    var token by remember { mutableStateOf(readSavedToken(context, prefs)) }
    var title by remember { mutableStateOf("Не подключено") }
    var projectName by remember { mutableStateOf("") }
    var status by remember { mutableStateOf(if (token.isBlank()) "Сканируй QR-код на ПК один раз" else "Подключаюсь…") }
    var usageLimits by remember { mutableStateOf<UsageLimits?>(null) }
    var limitsLoading by remember { mutableStateOf(false) }
    var limitsError by remember { mutableStateOf("") }
    var codexBusy by remember { mutableStateOf(false) }
    var selectedThreadId by remember { mutableStateOf(prefs.getString("selectedThreadId", "") ?: "") }
    var threadModel by remember { mutableStateOf("") }
    var threadEffort by remember { mutableStateOf("") }
    var selectedModel by remember { mutableStateOf("") }
    var selectedEffort by remember { mutableStateOf("") }
    val models = remember { mutableStateListOf<ModelOption>() }
    val projects = remember { mutableStateListOf<ProjectGroup>() }
    var input by remember { mutableStateOf(prefs.getString("draft:${selectedThreadId}", "") ?: "") }
    var delivering by remember { mutableStateOf(false) }
    var localQueueError by remember { mutableStateOf("") }
    var lines by remember { mutableStateOf<List<ChatLine>>(emptyList()) }
    var historyHasMore by remember { mutableStateOf(false) }
    var olderPagesLoaded by remember { mutableStateOf(false) }
    var loadingOlder by remember { mutableStateOf(false) }
    var historyError by remember { mutableStateOf("") }
    val files = remember { mutableStateListOf<PendingFile>().apply { addAll(readPendingFiles(prefs)) } }
    val pendingFileDeletes = remember { mutableStateListOf<String>().apply {
        addAll(prefs.getStringSet("pendingFileDeletes", emptySet()).orEmpty())
    } }
    val localQueue = remember { mutableStateListOf<LocalMessage>().apply { addAll(readLocalQueue(prefs)) } }
    val outboxFiles = remember { mutableStateListOf<RemoteFile>() }
    var outboxLoading by remember { mutableStateOf(false) }
    var outboxError by remember { mutableStateOf("") }
    var workspaceRoot by remember { mutableStateOf("") }
    var workspacePath by remember { mutableStateOf("") }
    var workspaceEntries by remember { mutableStateOf<List<WorkspaceEntry>>(emptyList()) }
    var workspaceLoading by remember { mutableStateOf(false) }
    var workspaceError by remember { mutableStateOf("") }
    var workspaceTruncated by remember { mutableStateOf(false) }
    var previewPath by remember { mutableStateOf("") }
    var previewText by remember { mutableStateOf("") }
    var previewNote by remember { mutableStateOf("") }
    var previewLoading by remember { mutableStateOf(false) }
    var transferProgress by remember { mutableStateOf<Pair<Long, Long>?>(null) }
    var transferringFileId by remember { mutableStateOf("") }
    var fileStatus by remember { mutableStateOf("") }
    var selectedOutbox by remember { mutableStateOf<RemoteFile?>(null) }
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
                item.optString("id"), item.optString("time"), attachments, images)
        }
    }

    suspend fun <T> withReachableHost(block: suspend (String) -> T): T {
        val choices = listOf(activeHost, host, tailHost).filter { it.isNotBlank() }.distinct()
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

    suspend fun refresh() {
        if (token.isBlank()) return
        val requestedThreadId = selectedThreadId
        val threadQuery = if (requestedThreadId.isBlank()) "" else "?threadId=${Uri.encode(requestedThreadId)}"
        val pair = withReachableHost { root ->
            withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/status$threadQuery", token) to
                    requestJson(client, "$root/api/history$threadQuery", token)
            }
        }
        val (state, history) = pair
            withContext(Dispatchers.Main) {
                if (requestedThreadId.isNotBlank() && requestedThreadId != selectedThreadId) return@withContext
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
                val latest = parseHistory(history)
                if (lines.isEmpty()) {
                    lines = latest
                    historyHasMore = history.optBoolean("hasMore")
                } else {
                    val latestIds = latest.mapTo(HashSet()) { it.id }
                    val combined = LinkedHashMap<String, ChatLine>()
                    lines.filterNot { it.id in latestIds }.forEach { combined[it.id] = it }
                    latest.forEach { combined[it.id] = it }
                    lines = combined.values.toList()
                    if (!olderPagesLoaded) historyHasMore = history.optBoolean("hasMore")
                }
            }
    }

    suspend fun browseWorkspace(path: String) {
        val thread = selectedThreadId
        workspacePath = path
        workspaceLoading = true
        workspaceError = ""
        workspaceEntries = emptyList()
        previewPath = ""
        try {
            val result = withReachableHost { root -> withContext(Dispatchers.IO) {
                requestJson(client, "$root/api/workspace?threadId=${Uri.encode(thread)}&path=${Uri.encode(path)}", token)
            } }
            if (thread == selectedThreadId && path == workspacePath) {
                workspaceRoot = result.optString("rootName")
                val entries = result.optJSONArray("entries") ?: JSONArray()
                workspaceEntries = (0 until entries.length()).mapNotNull { index ->
                    val item = entries.optJSONObject(index) ?: return@mapNotNull null
                    WorkspaceEntry(item.optString("name"), item.optString("path"),
                        item.optBoolean("isDirectory"), item.optLong("size"))
                }
                workspaceTruncated = result.optBoolean("truncated")
            }
        } catch (error: Exception) {
            if (thread == selectedThreadId && path == workspacePath)
                workspaceError = "Не удалось открыть папку: ${error.message}"
        } finally {
            if (thread == selectedThreadId && path == workspacePath) workspaceLoading = false
        }
    }

    suspend fun previewWorkspace(path: String) {
        if (path.isBlank()) { previewPath = ""; return }
        val thread = selectedThreadId
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
            if (thread == selectedThreadId && path == previewPath) {
                if (result.optBoolean("previewable")) {
                    previewText = result.optString("text")
                    if (result.optBoolean("truncated")) previewNote = "Показано начало файла. Полную версию можно сохранить."
                } else previewNote = result.optString("reason", "Предпросмотр недоступен")
            }
        } catch (error: Exception) {
            if (thread == selectedThreadId && path == previewPath)
                previewNote = "Не удалось открыть файл: ${error.message}"
        } finally {
            if (thread == selectedThreadId && path == previewPath) previewLoading = false
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
        if (token.isBlank()) return
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
            if (threads.isNotEmpty()) projects.add(ProjectGroup(group.optString("id"), group.optString("name"),
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
                result.optLong("updatedAt"))
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
        selectedThreadId = threadId
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
        refresh()
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
        saveToken(context, prefs, token)
        prefs.edit().putString("host", host).putString("tailHost", tailHost)
            .putString("certificatePin", certificatePin).apply()
        status = "Подключено"
    }

    suspend fun cancelQueued(messageId: String) {
        val body = JSONObject().put("clientMessageId", messageId).toString()
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
            else "Сообщение убрано из очереди"
    }

    suspend fun deliverPending() {
        if (token.isBlank() || delivering || localQueue.isEmpty()) return
        delivering = true
        try {
            for (message in localQueue.toList()) {
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
                if (result.optBoolean("queued")) {
                    val index = localQueue.indexOfFirst { it.id == message.id }
                    if (index >= 0) localQueue[index] = localQueue[index].copy(acceptedByBridge = true)
                } else {
                    localQueue.removeAll { it.id == message.id }
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
                token = ""
                lines = emptyList()
                projects.clear()
                models.clear()
                usageLimits = null
                files.clear()
                savePendingFiles(prefs, emptyList())
                pendingFileDeletes.clear()
                savePendingDeletes(prefs, emptyList())
                clearSavedToken(context, prefs)
                status = "Телефон отключён"
            }.onFailure { status = "Не удалось отключить телефон: ${it.message}" }
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
            while (true) {
                runCatching { cleanupPendingFiles() }
                runCatching { deliverPending() }
                    .onFailure {
                        localQueueError = it.message ?: "жду связи с ПК"
                        status = "Сообщение на телефоне: $localQueueError"
                    }
                runCatching { refresh() }.onFailure { error ->
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
    MaterialTheme(colorScheme = if (darkTheme) darkPalette else lightPalette) {
        CompanionUi(
            themeMode = themeMode,
            onThemeMode = { value ->
                themeMode = value
                prefs.edit().putString("themeMode", value).apply()
            },
            paired = token.isNotBlank(), title = title, projectName = projectName,
            status = status, usageLimits = usageLimits, limitsLoading = limitsLoading,
            limitsError = limitsError, lines = lines, queue = localQueue.toList(),
            selectedThreadId = selectedThreadId, projects = projects.toList(),
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
            onBrowseWorkspace = { path -> scope.launch { browseWorkspace(path) } },
            onPreviewWorkspace = { path -> scope.launch { previewWorkspace(path) } },
            onAskWorkspace = { path ->
                val prompt = "Посмотри файл `$path` в проекте. "
                input = if (input.isBlank()) prompt else "$input\n$prompt"
                prefs.edit().putString("draft:$selectedThreadId", input).apply()
            },
            onSaveWorkspace = { entry ->
                selectedOutbox = RemoteFile("", entry.name, entry.size, entry.path, selectedThreadId)
                fileStatus = ""
                savePicker.launch(entry.name)
            },
            onLoadOlder = { scope.launch { loadOlderHistory() } },
            loadImage = { endpoint ->
                withReachableHost { root -> withContext(Dispatchers.IO) {
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
                        val bytes = output.toByteArray()
                        if (bytes.size > limit) error("Изображение слишком большое")
                        bytes
                    }
                } }
            },
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
                val index = localQueue.indexOfFirst { it.id == message.id }
                if (index >= 0) {
                    localQueue[index] = localQueue[index].copy(cancelRequested = true)
                    saveLocalQueue(prefs, localQueue.toList())
                    scope.launch { runCatching { cancelQueued(message.id) }
                        .onFailure { status = "Отмена ждёт связи с ПК" } }
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

private suspend fun downloadFile(
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
    try { call.execute().use { response ->
            if (!response.isSuccessful) error("ПК вернул ошибку ${response.code}")
            val expected = response.header("X-Content-SHA256") ?: error("Нет контрольной суммы")
            val digest = MessageDigest.getInstance("SHA-256")
            val stream = response.body?.byteStream() ?: error("Файл пуст")
            val total = response.body?.contentLength()?.takeIf { it > 0 } ?: file.size
            var written = 0L
            context.contentResolver.openOutputStream(destination, "rwt")?.use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = stream.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                    written += count
                    withContext(Dispatchers.Main) { onProgress(written, total) }
                }
            } ?: error("Не удалось записать файл")
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            check(actual.equals(expected, ignoreCase = true)) { "Контрольная сумма не совпала" }
    } } finally { cancellation.dispose(); activeCall.compareAndSet(call, null) }
}

private fun requestJson(client: OkHttpClient, url: String, token: String): JSONObject {
    val request = Request.Builder().url(url).header("Authorization", "Bearer $token").get().build()
    return client.newCall(request).execute().use { response ->
        val json = JSONObject(response.body?.string().orEmpty())
        if (!response.isSuccessful) error(json.optString("error", "Запрос не удался"))
        json
    }
}

private fun pinnedClient(fingerprintInput: String): OkHttpClient {
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

private fun saveToken(context: Context, prefs: android.content.SharedPreferences, token: String) {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, tokenKey()) }
    val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
    prefs.edit()
        .putString("tokenIv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
        .putString("tokenCiphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        .apply()
}

private fun readSavedToken(context: Context, prefs: android.content.SharedPreferences): String {
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
