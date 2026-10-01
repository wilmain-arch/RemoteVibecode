package ru.wilmain.codexphone

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Base64
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

private const val UPDATE_REPO = "wilmain-arch/RemoteVibecode"
private const val UPDATE_API = "https://api.github.com/repos/$UPDATE_REPO/releases/latest"
private val UPDATE_KEY = """
-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA2McfifrSER7Sb+GQnYKh
2deeNzJSNKmodmh9Ik3nxkQiZZxgUvgUOHGg1P8A18HrustyZX1ou5vK9wqDh1xS
60R7ZGPuKF26WDPfEZ9FO7TvXmQPl0/vQCRdxQPolBI0wIvGGHnZAehD6QUlcc4/
jCZkipq6yks1Uh6Mepz2/JjoJ2R8aVsyRfRx9hymKMil3otdk12WIdd2GwfR5E9d
LQ2ZvqPJfdzXV84IHfu6MgBmjn0Pb22xlafTxwHqoreV/uJE6qLh3M6LvXr6eoKe
/ek7LfDMKxIvw1eGvSf7qH2EPyut4s18as7w/cCRCCnoKmuxvcHsJKdV2TRyAlv9
WrBRJDHU/p7DgWQ95/xmiGtCGDuD3aoVBE4XQ42pgdexSHAFVeWdByBCNXPLdCjL
7tGuibVgYT/mFpS5EKjp4c226qaHuGdezPpzTqWrPsExVoWX7XbZDT6uqjrxTAxd
abN6eXCkAngheBar1caGuaBkoW4mSUV5uif6OJirMFt3AgMBAAE=
-----END PUBLIC KEY-----
""".trimIndent()

internal data class AndroidUpdate(val version: String, val code: Long, val url: String,
    val sha256: String, val size: Long, val certificate: String, val notes: String)

internal class AppUpdates(private val context: Context, private val scope: CoroutineScope) {
    private val prefs = context.getSharedPreferences("updates", Context.MODE_PRIVATE)
    // System TLS trust; never use the bridge client or its token/certificate pin.
    private val client = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS).callTimeout(10, TimeUnit.MINUTES).build()
    var checking by mutableStateOf(false); private set
    var downloading by mutableStateOf(false); private set
    var progress by mutableStateOf(0); private set
    var message by mutableStateOf(""); private set
    var update by mutableStateOf<AndroidUpdate?>(null); private set
    var ready by mutableStateOf(false); private set
    var checkedAt by mutableStateOf(prefs.getLong("checkedAt", 0)); private set
    private val call = AtomicReference<Call?>(null)
    private var downloadJob: Job? = null
    private val directory = File(context.cacheDir, "updates")
    private val apk = File(directory, "RemoteVibecode.apk")

    private fun parseManifest(raw: ByteArray, sig: ByteArray, tag: String, urls: Set<String>? = null): AndroidUpdate {
                    val keyBytes = Base64.decode(UPDATE_KEY.replace("-----BEGIN PUBLIC KEY-----", "")
                        .replace("-----END PUBLIC KEY-----", ""), Base64.DEFAULT)
                    val key = KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(keyBytes))
                    require(Signature.getInstance("SHA256withRSA").run {
                        initVerify(key); update(raw); verify(sig)
                    }) { "Подпись обновления не подтверждена" }
                    val manifest = JSONObject(String(raw, Charsets.UTF_8))
                    require(manifest.getInt("schema") == 1 && tag == "bundle-v" + manifest.getString("bundle")) { "Неверный формат обновления" }
                    val item = manifest.getJSONObject("components").getJSONObject("android")
                    require(item.getString("packageId") == context.packageName) { "Этот релиз предназначен для другого приложения" }
                    require(item.getInt("protocol") == 1) { "Требуется ручное обновление компонентов" }
                    val url = source(item.getString("url"))
                    require(urls == null || url in urls) { "Файл отсутствует в релизе" }
                    val hash = item.getString("sha256")
                    val cert = item.getString("certificateSha256")
                    require(hash.matches(Regex("[a-f0-9]{64}")) && cert.matches(Regex("[a-f0-9]{64}"))) { "Некорректная контрольная сумма" }
                    val size = item.getLong("size")
                    require(size in 1..(300L * 1024 * 1024)) { "Некорректный размер обновления" }
                    return AndroidUpdate(item.getString("version"), item.getLong("versionCode"), url, hash, size, cert, manifest.getString("notes"))
    }
    init {
        try {
            val cached = JSONObject(File(directory, "metadata.json").readText())
            val info = parseManifest(Base64.decode(cached.getString("raw"), Base64.DEFAULT),
                Base64.decode(cached.getString("signature"), Base64.DEFAULT), cached.getString("tag"))
            update = info.takeIf { it.code > BuildConfig.VERSION_CODE }
            message = if (update != null) "Доступна версия ${info.version}" else "Установлена актуальная версия"
        } catch (_: Exception) { /* Missing/invalid cache triggers a fresh automatic check. */ }
    }
    private fun source(url: String): String {
        val uri = Uri.parse(url)
        require(uri.scheme == "https" && uri.authority == "github.com" &&
            uri.path.orEmpty().startsWith("/$UPDATE_REPO/releases/download/bundle-v") &&
            uri.query == null && uri.fragment == null) { "Неизвестный источник обновления" }
        return url
    }
    private fun get(url: String, max: Long): ByteArray {
        val req = Request.Builder().url(url).header("User-Agent", "RemoteVibecode-updater")
            .header("Accept", if (url == UPDATE_API) "application/vnd.github+json" else "application/octet-stream").build()
        client.newCall(req).execute().use { response ->
            require(response.isSuccessful) {
                if (response.code == 403 || response.code == 429) "GitHub ограничил запросы. Попробуйте позже."
                else "GitHub недоступен (HTTP ${response.code})"
            }
            require(response.request.url.isHttps) { "Небезопасное перенаправление" }
            val body = response.body ?: error("Пустой ответ GitHub")
            val bytes = body.byteStream().use { input ->
                val output = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                while (output.size() <= max) {
                    val n = input.read(buffer, 0, minOf(buffer.size.toLong(), max + 1 - output.size()).toInt())
                    if (n < 0) break
                    output.write(buffer, 0, n)
                }
                output.toByteArray()
            }
            require(bytes.size <= max) { "Ответ слишком большой" }
            return bytes
        }
    }
    fun check(manual: Boolean = true) {
        if (checking || downloading) return
        val now = System.currentTimeMillis()
        if (!manual && now - prefs.getLong("lastAttempt", 0) < 86_400_000) return
        prefs.edit().putLong("lastAttempt", now).apply()
        checking = true
        if (manual) message = "Проверяю обновления…"
        scope.launch {
            try {
                val info = withContext(Dispatchers.IO) {
                    val release = JSONObject(String(get(UPDATE_API, 2 * 1024 * 1024), Charsets.UTF_8))
                    require(!release.optBoolean("draft") && !release.optBoolean("prerelease")) { "Ожидался стабильный релиз" }
                    val assets = release.getJSONArray("assets")
                    val urls = (0 until assets.length()).associate {
                        val asset = assets.getJSONObject(it); asset.getString("name") to asset.getString("browser_download_url")
                    }
                    val raw = get(source(urls["update.json"] ?: error("Релиз ещё не поддерживает встроенное обновление")), 512 * 1024)
                    val sig = Base64.decode(get(source(urls["update.json.sig"] ?: error("Нет подписи обновления")), 4096), Base64.DEFAULT)
                    val info = parseManifest(raw, sig, release.getString("tag_name"), urls.values.toSet())
                    directory.mkdirs()
                    File(directory, "metadata.json").writeText(JSONObject()
                        .put("raw", Base64.encodeToString(raw, Base64.NO_WRAP))
                        .put("signature", Base64.encodeToString(sig, Base64.NO_WRAP))
                        .put("tag", release.getString("tag_name")).toString())
                    info
                }
                if (update?.sha256 != info.sha256) ready = false
                update = info.takeIf { it.code > BuildConfig.VERSION_CODE }
                checkedAt = System.currentTimeMillis()
                prefs.edit().putLong("checkedAt", checkedAt).apply()
                message = if (update != null) "Доступна версия ${info.version}" else "Установлена актуальная версия"
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { message = "Не удалось проверить: ${e.message ?: "нет связи"}" }
            finally { checking = false }
        }
    }
    fun download() {
        val info = update ?: return
        if (downloading) return
        downloading = true; ready = false; progress = 0; message = "Скачиваю обновление…"
        downloadJob = scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    directory.mkdirs()
                    val part = File(directory, "update.part")
                    try {
                        val request = Request.Builder().url(source(info.url)).header("User-Agent", "RemoteVibecode-updater").build()
                        val activeCall = client.newCall(request)
                        call.set(activeCall)
                        activeCall.execute().use { response ->
                            require(response.isSuccessful && response.request.url.isHttps) { "Не удалось скачать обновление" }
                            val digest = MessageDigest.getInstance("SHA-256")
                            var count = 0L
                            response.body?.byteStream()?.use { input ->
                                part.outputStream().use { output ->
                                    val buffer = ByteArray(65536)
                                    while (true) {
                                        currentCoroutineContext().ensureActive()
                                        val n = input.read(buffer)
                                        if (n < 0) break
                                        count += n
                                        require(count <= info.size) { "Размер обновления не совпадает" }
                                        output.write(buffer, 0, n); digest.update(buffer, 0, n)
                                        val percent = (count * 100 / info.size).toInt()
                                        withContext(Dispatchers.Main) { progress = percent }
                                    }
                                }
                            } ?: error("Пустой файл обновления")
                            require(count == info.size && digest.digest().hex() == info.sha256) { "Контрольная сумма не совпадает" }
                        }
                        verifyApk(part, info)
                        require(part.renameTo(apk)) { "Не удалось сохранить APK" }
                    } finally { part.delete(); call.set(null) }
                }
                ready = true; message = "APK проверен. Можно установить обновление."
            } catch (e: CancellationException) { message = "Скачивание отменено"; throw e }
            catch (e: Exception) { message = "Не удалось скачать: ${e.message}" }
            finally { downloading = false }
        }
    }
    fun cancel() { call.get()?.cancel(); downloadJob?.cancel() }
    @Suppress("DEPRECATION")
    private fun verifyApk(file: File, info: AndroidUpdate) {
        val pm = context.packageManager
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val installed = pm.getPackageInfo(context.packageName, flags)
        val archive = pm.getPackageArchiveInfo(file.absolutePath, flags) ?: error("Некорректный APK")
        require(archive.packageName == context.packageName) { "APK другого приложения" }
        val code = if (Build.VERSION.SDK_INT >= 28) archive.longVersionCode else archive.versionCode.toLong()
        require(code == info.code && code > BuildConfig.VERSION_CODE) { "Версия APK не совпадает" }
        fun signatures(p: android.content.pm.PackageInfo) =
            (if (Build.VERSION.SDK_INT >= 28) p.signingInfo?.apkContentsSigners else p.signatures)
                ?.map { MessageDigest.getInstance("SHA-256").digest(it.toByteArray()).hex() }?.toSet().orEmpty()
        val expected = signatures(installed)
        require(expected.isNotEmpty() && signatures(archive) == expected && info.certificate in expected) { "Подпись APK не совпадает с установленным приложением" }
    }
    suspend fun installIntent(): Intent {
        val info = update ?: error("Сначала проверьте обновления")
        withContext(Dispatchers.IO) {
            require(ready && apk.isFile && apk.length() == info.size) { "Сначала скачайте обновление" }
            require(apk.inputStream().use { input ->
                val digest = MessageDigest.getInstance("SHA-256"); val buffer = ByteArray(65536)
                while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
                digest.digest().hex()
            } == info.sha256) { "APK изменился после проверки" }
            verifyApk(apk, info)
        }
        val uri = FileProvider.getUriForFile(context, context.packageName + ".updates", apk)
        return Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    fun installError(e: Exception) { message = "Не удалось открыть установку: ${e.message}" }
}
private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }

@Composable
internal fun UpdatesScreen(updates: AppUpdates, onClose: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var permissionPending by rememberSaveableState()
    fun install() { scope.launch {
        try { context.startActivity(updates.installIntent()) }
        catch (e: Exception) { updates.installError(e) }
    } }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (permissionPending && context.packageManager.canRequestPackageInstalls()) install()
        permissionPending = false
    }
    BackHandler(onBack = onClose)
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { UiScreenHeader(title = "Обновления", onBack = onClose) },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState())
            .padding(22.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("RemoteVibecode ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.titleMedium)
            Text(updates.message.ifBlank { "Обновления загружаются из официальных релизов GitHub." })
            if (updates.checkedAt > 0) Text("Проверено: " + android.text.format.DateFormat.format("dd.MM.yyyy HH:mm", updates.checkedAt),
                style = MaterialTheme.typography.bodySmall)
            Button(onClick = { updates.check() }, enabled = !updates.checking && !updates.downloading) {
                Text(if (updates.checking) "Проверяю…" else "Проверить обновления")
            }
            updates.update?.let { info ->
                Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = androidx.compose.foundation.shape.RoundedCornerShape(20.dp)) {
                    Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("Версия ${info.version}", style = MaterialTheme.typography.titleMedium)
                        Text("Размер: ${info.size / 1024 / 1024} МБ · Привязка и настройки сохранятся")
                        if (updates.downloading) {
                            LinearProgressIndicator(progress = { updates.progress / 100f }, modifier = Modifier.fillMaxWidth())
                            Text("Скачано ${updates.progress}%")
                            TextButton(onClick = updates::cancel) { Text("Отменить скачивание") }
                        } else Button(onClick = {
                            if (!updates.ready) updates.download()
                            else if (context.packageManager.canRequestPackageInstalls()) install()
                            else {
                                permissionPending = true
                                try { permission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + context.packageName))) }
                                catch (e: Exception) { permissionPending = false; updates.installError(e) }
                            }
                        }) { Text(if (updates.ready) "Установить обновление" else "Скачать обновление") }
                        Text("Android попросит подтвердить установку. Если запрос отменён, нажмите «Установить обновление» снова.", style = MaterialTheme.typography.bodySmall)
                    }
                }
                Text("Что изменилось", style = MaterialTheme.typography.titleMedium)
                MarkdownContent(info.notes)
            }
        }
    }
}
@Composable
private fun rememberSaveableState() = androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf(false) }
