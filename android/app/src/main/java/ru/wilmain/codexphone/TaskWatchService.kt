package ru.wilmain.codexphone

import android.Manifest
import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import org.json.JSONArray
import java.util.concurrent.ConcurrentHashMap

/** Explicitly enabled monitoring of running tasks, including while the Activity is closed. */
class TaskWatchService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val watches = ConcurrentHashMap<String, String>()
    private var worker: Job? = null
    private val preferences by lazy { getSharedPreferences("companion", Context.MODE_PRIVATE) }
    private val notifications by lazy { getSystemService(NotificationManager::class.java) }

    override fun onCreate() {
        super.onCreate()
        notifications.createNotificationChannel(NotificationChannel("task-watch", "Наблюдение за задачами", NotificationManager.IMPORTANCE_LOW))
        notifications.createNotificationChannel(NotificationChannel("task-result", "Завершение и вопросы Codex", NotificationManager.IMPORTANCE_DEFAULT))
        if (preferences.getString("taskWatchIdentity", "") == identity(this)) {
            val saved = runCatching { org.json.JSONObject(preferences.getString("taskWatches", "{}").orEmpty()) }.getOrDefault(org.json.JSONObject())
            saved.keys().forEach { key -> watches[key] = saved.optString(key) }
        } else clearSaved(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val thread = intent?.getStringExtra("threadId").orEmpty()
        val turn = intent?.getStringExtra("turnId").orEmpty()
        if (!preferences.getBoolean("taskNotifications", false) || (watches.isEmpty() && (thread.isBlank() || turn.isBlank()))) {
            stopSelf(); return START_NOT_STICKY
        }
        if (thread.isNotBlank() && turn.isNotBlank()) watches.putIfAbsent(thread, turn)
        persistWatches()
        val notification = statusNotification("Ожидаю завершения Codex")
        if (Build.VERSION.SDK_INT >= 29) startForeground(7001, notification,
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0)
        else startForeground(7001, notification)
        if (worker?.isActive != true) worker = scope.launch {
            do { withContext(Dispatchers.IO) { monitor() } }
            while (watches.isNotEmpty() && preferences.getBoolean("taskNotifications", false))
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
        return START_STICKY
    }

    private fun persistWatches() {
        preferences.edit().putString("taskWatches", org.json.JSONObject(watches.toMap()).toString())
            .putString("taskWatchIdentity", identity(this)).commit()
    }

    private suspend fun monitor() {
        val pin = preferences.getString("certificatePin", "").orEmpty()
        val initialHost = preferences.getString("host", "").orEmpty()
        val initialToken = readSavedToken(this, preferences)
        val client = pinnedClient(pin)
        try {
            while (currentCoroutineContext().isActive && watches.isNotEmpty()) {
                if (!preferences.getBoolean("taskNotifications", false)) break
                val token = readSavedToken(this, preferences)
                if (token.isBlank() || token != initialToken || preferences.getString("certificatePin", "") != pin ||
                    preferences.getString("host", "") != initialHost) { watches.clear(); clearSaved(this); break }
                val host = preferences.getString("host", "").orEmpty()
                val tail = preferences.getString("tailHost", "").orEmpty()
                val hosts = (if (BuildConfig.RELAY_ONLY) listOf(host) else listOf(host, tail)).filter { it.startsWith("https://") }.distinct()
                for ((thread, expectedTurn) in watches.toMap()) {
                    var connected = false
                    for (root in hosts) {
                        try {
                            val query = "?threadId=${Uri.encode(thread)}"
                            val task = requestJson(client, "$root/api/task$query&turnId=${Uri.encode(expectedTurn)}", token)
                            val requests = (requestJson(client, "$root/api/requests$query", token).optJSONArray("requests") ?: JSONArray()).objects()
                            requests.forEach { prompt ->
                                notifyOnce("request:${prompt.optString("id")}", thread, "Codex ожидает ответа", "Откройте чат, чтобы ответить на вопрос или запрос разрешения.")
                            }
                            finishIfReady(task, thread, expectedTurn)
                            persistWatches()
                            connected = true
                            break
                        } catch (cancel: CancellationException) { throw cancel }
                        catch (_: Exception) { /* Keep the watch: a network failure never means completion. */ }
                    }
                    if (watches.isNotEmpty()) notifications.notify(7001, statusNotification(if (connected) "Наблюдаю за задачами Codex" else "Связь потеряна · повторяю подключение"))
                }
                if (watches.isEmpty()) break
                delay(4000)
            }
        } finally {
            client.dispatcher.executorService.shutdown()
            client.connectionPool.evictAll()
        }
    }

    private fun finishIfReady(task: org.json.JSONObject, thread: String, expectedTurn: String) {
        val outcome = task.optString("status")
        if (task.optBoolean("found") && outcome in setOf("completed", "failed", "interrupted", "cancelled")) {
            val title = when (outcome) {
                "completed" -> "Codex завершил работу"
                "failed" -> "Задача завершилась с ошибкой"
                else -> "Работа Codex прервана"
            }
            notifyOnce("outcome:$expectedTurn", thread, title,
                listOf(task.optString("outcomeSummary"), task.optString("quotaSummary"))
                    .filter { it.isNotBlank() }.joinToString(" · "))
            val nextTurn = task.optString("activeTurnId")
            if (nextTurn.isNotBlank() && nextTurn != expectedTurn) watches.replace(thread, expectedTurn, nextTurn)
            else if (task.optInt("queuedCount") == 0) watches.remove(thread, expectedTurn)
        } else if (!task.optBoolean("found")) {
            notifyOnce("unavailable:$expectedTurn", thread, "Задача больше недоступна", "Проверьте выбранный чат на ПК.")
            watches.remove(thread, expectedTurn)
        }
    }

    private fun notifyOnce(key: String, thread: String, title: String, text: String) {
        val seen = preferences.getStringSet("taskNotificationsSeen", emptySet()).orEmpty()
        if (key in seen) return
        if (!canNotify(this)) return
        val next = (seen.toList() + key).takeLast(200).toSet()
        notifications.notify(key.hashCode(), NotificationCompat.Builder(this, "task-result")
            .setSmallIcon(R.drawable.ui_terminal).setContentTitle(title).setContentText(text.ifBlank { "Откройте чат для подробностей" })
            .setStyle(NotificationCompat.BigTextStyle().bigText(text)).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setAutoCancel(true).setContentIntent(openChat(thread)).build())
        preferences.edit().putStringSet("taskNotificationsSeen", next).apply()
    }

    private fun openChat(thread: String) = PendingIntent.getActivity(this, thread.hashCode(),
        Intent(this, MainActivity::class.java).putExtra("openThreadId", thread)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun statusNotification(text: String) = NotificationCompat.Builder(this, "task-watch")
        .setSmallIcon(R.drawable.ui_terminal).setContentTitle("RemoteVibecode").setContentText(text)
        .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(openChat(watches.keys.firstOrNull().orEmpty())).build()

    override fun onDestroy() { scope.cancel(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private fun identity(context: Context): String {
            val prefs = context.getSharedPreferences("companion", Context.MODE_PRIVATE)
            val source = listOf(prefs.getString("host", ""), prefs.getString("certificatePin", ""), readSavedToken(context, prefs)).joinToString("\n")
            return java.security.MessageDigest.getInstance("SHA-256").digest(source.toByteArray()).joinToString("") { "%02x".format(it) }
        }
        internal fun clearSaved(context: Context) {
            context.getSharedPreferences("companion", Context.MODE_PRIVATE).edit()
                .remove("taskWatches").remove("taskWatchIdentity").commit()
        }
        internal fun restore(context: Context) {
            val prefs = context.getSharedPreferences("companion", Context.MODE_PRIVATE)
            if (prefs.getBoolean("taskNotifications", false) && canNotify(context) && prefs.getString("taskWatchIdentity", "") == identity(context) && prefs.getString("taskWatches", "{}").orEmpty() != "{}")
                ContextCompat.startForegroundService(context, Intent(context, TaskWatchService::class.java))
        }
        internal fun canNotify(context: Context) = Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        internal fun watch(context: Context, threadId: String, turnId: String) {
            ContextCompat.startForegroundService(context, Intent(context, TaskWatchService::class.java)
                .putExtra("threadId", threadId).putExtra("turnId", turnId))
        }
    }
}
