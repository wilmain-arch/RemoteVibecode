package ru.wilmain.codexphone

import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.foundation.clickable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID

private data class Subagent(val id: String, val parentId: String, val name: String,
    val model: String, val role: String, val task: String, val status: String, val message: String, val canSend: Boolean)

private data class SubagentDraft(val text: String = "", val messageId: String = UUID.randomUUID().toString())

private fun agentStatus(status: String) = when (status) {
    "running", "active" -> "Работает"
    "pendingInit" -> "Запускается"
    "completed" -> "Завершил работу"
    "interrupted" -> "Остановлен"
    "errored", "systemError" -> "Ошибка"
    "shutdown" -> "Закрыт"
    "notFound" -> "Недоступен"
    "idle" -> "Ожидает"
    "notLoaded" -> "Не загружен"
    else -> "Статус неизвестен"
}

@Composable
internal fun SubagentsScreen(threadId: String, onClose: () -> Unit,
    request: suspend (String, JSONObject?) -> JSONObject, loadImage: suspend (String) -> ByteArray,
    onProjectFile: (String) -> Unit, onSaveImage: (ChatImage) -> Unit, saveStatus: String = "",
    onContextFile: (String, String) -> Unit = { reference, _ -> onProjectFile(reference) }) {
    var openedImage by rememberSaveable(stateSaver = ChatImageSaver) { mutableStateOf<ChatImage?>(null) }
    val scope = rememberCoroutineScope()
    var agents by remember(threadId) { mutableStateOf(emptyList<Subagent>()) }
    var selectedId by rememberSaveable(threadId) { mutableStateOf<String?>(null) }
    var loading by remember(threadId) { mutableStateOf(true) }
    var error by remember(threadId) { mutableStateOf("") }
    var notice by remember(selectedId) { mutableStateOf("") }
    var history by remember(selectedId) { mutableStateOf(emptyList<JSONObject>()) }
    var cursor by remember(selectedId) { mutableStateOf<String?>(null) }
    var historyReady by remember(selectedId) { mutableStateOf(false) }
    var historyBusy by remember(selectedId) { mutableStateOf(false) }
    val drafts = rememberSaveable(threadId, saver = listSaver(
        save = { map: androidx.compose.runtime.snapshots.SnapshotStateMap<String, SubagentDraft> ->
            map.entries.flatMap { listOf(it.key, it.value.text, it.value.messageId) }
        },
        restore = { values -> mutableStateMapOf<String, SubagentDraft>().apply {
            values.chunked(3).forEach { put(it[0], SubagentDraft(it[1], it[2])) }
        } }
    )) { mutableStateMapOf<String, SubagentDraft>() }
    val emptyDraft = remember(threadId, selectedId) { SubagentDraft() }
    val draft = selectedId?.let { drafts[it] } ?: emptyDraft
    val input = draft.text
    val inputLength = input.codePointCount(0, input.length)
    var actionBusy by remember { mutableStateOf(false) }
    var confirmStop by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    val selected = agents.firstOrNull { it.id == selectedId }
    val listState = remember(selectedId) { androidx.compose.foundation.lazy.LazyListState() }
    val rootQuery = "threadId=${Uri.encode(threadId)}"
    fun back() { if (actionBusy) return; if (selectedId != null) selectedId = null else onClose() }
    BackHandler { back() }
    LaunchedEffect(threadId, selectedId, refresh) {
        while (true) {
            try {
                val array = request("subagents?$rootQuery", null).optJSONArray("agents")
                agents = (0 until (array?.length() ?: 0)).mapNotNull { index ->
                    val item = array?.optJSONObject(index) ?: return@mapNotNull null
                    Subagent(item.optString("id"), item.optString("parentId"), item.optString("name"),
                        item.optString("model").takeUnless { it == "null" } ?: "", item.optString("role"), item.optString("task"), item.optString("status"),
                        item.optString("message"), item.optBoolean("canSend"))
                }
                val id = selectedId
                if (id != null) {
                    val result = request("subagents/history?$rootQuery&agentId=${Uri.encode(id)}", null)
                    val rows = result.optJSONArray("turns")
                    val recent = (0 until (rows?.length() ?: 0)).mapNotNull { rows?.optJSONObject(it) }
                    val nearBottom = !historyReady || !listState.canScrollForward
                    val freshIds = recent.map { it.optString("id") }.toSet()
                    val overlap = history.indexOfFirst { it.optString("id") in freshIds }
                    val updatedHistory = (if (overlap >= 0) history.take(overlap) else emptyList()) + recent
                    val contentChanged = history.map { it.toString() } != updatedHistory.map { it.toString() }
                    history = updatedHistory
                    if (!historyReady || overlap < 0) cursor = result.optString("nextBefore").takeIf {
                        result.optBoolean("hasMore") && it.isNotBlank() && it != "null"
                    }
                    historyReady = true
                    if (contentChanged && nearBottom && history.isNotEmpty() && !listState.isScrollInProgress) {
                        withFrameNanos { }
                        if (!listState.isScrollInProgress) {
                            listState.scrollToItem(history.size + 1)
                            listState.scroll { scrollBy(100_000f) }
                        }
                    }
                }
                error = ""
            } catch (cancel: CancellationException) { throw cancel }
            catch (failure: Exception) { error = failure.message ?: "Нет связи с агентом ПК" }
            finally { loading = false }
            delay(4000)
        }
    }
    fun act(action: String) {
        val id = selectedId ?: return
        if (actionBusy) return
        val outgoingDraft = draft
        actionBusy = true
        scope.launch {
            try {
                val payload = JSONObject().put("threadId", threadId).put("agentId", id)
                    .put("action", action).put("text", outgoingDraft.text).put("messageId", outgoingDraft.messageId)
                val result = request("subagents/action", payload)
                notice = result.optString("message")
                if (action == "send") drafts.remove(id)
                refresh++
            } catch (cancel: CancellationException) { throw cancel }
            catch (failure: Exception) { notice = "Не удалось выполнить: ${failure.message}" }
            finally { actionBusy = false }
        }
    }
    var changesOpen by rememberSaveable(selectedId) { mutableStateOf(false) }
    if (changesOpen && selectedId != null) {
        ChangesScreen(selectedId.orEmpty(), request, { changesOpen = false }) { onContextFile(it, selectedId.orEmpty()) }
        return
    }
    Scaffold(containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            UiScreenHeader(selected?.name ?: "Субагенты", ::back,
                subtitle = if (selectedId == null) "${agents.count { it.status == "running" }} работают · ${agents.size} всего"
                    else selected?.let { agentStatus(it.status) + if (it.model.isBlank()) "" else " · ${it.model}" } ?: "Загрузка",
                actions = { IconButton(onClick = { refresh++ }) { UiGlyph(UiIcon.Refresh, "Обновить", 22.dp) } })
        }, bottomBar = {
            if (selectedId != null) Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)
                .imePadding().navigationBarsPadding().padding(UiSpace.screen)) {
                TaskControls(selectedId.orEmpty(), "", request, onChanges = { changesOpen = true })
                TextButton(onClick = { changesOpen = true }) { Text("Изменения файлов") }
                if (notice.isNotBlank()) Text(notice, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(bottom = 8.dp))
                if (selected?.canSend == true) {
                    TextField(input, { value ->
                        val id = selectedId ?: return@TextField
                        val text = if (value.codePointCount(0, value.length) > 32000)
                            value.substring(0, value.offsetByCodePoints(0, 32000)) else value
                        if (text != input) drafts[id] = SubagentDraft(text)
                    },
                        modifier = Modifier.fillMaxWidth(), placeholder = { Text("Уточнение или новая задача") },
                        supportingText = { Text("$inputLength / 32 000", Modifier.fillMaxWidth(),
                            textAlign = androidx.compose.ui.text.style.TextAlign.End) },
                        maxLines = 4, enabled = !actionBusy, shape = RoundedCornerShape(18.dp),
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                            unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                            focusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent,
                            unfocusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        if (selected.status == "running") TextButton(onClick = { confirmStop = true },
                            enabled = !actionBusy) { Text("Остановить", color = if (actionBusy) MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f) else MaterialTheme.colorScheme.error) }
                        TextButton(onClick = { act("send") }, enabled = input.isNotBlank() && !actionBusy) {
                            Text(if (selected.status == "running") "Корректировать" else "Отправить")
                        }
                    }
                } else {
                    Text("Desktop не разрешает прямой ввод этому агенту.", style = MaterialTheme.typography.bodySmall)
                    if (selected?.status == "running") TextButton(onClick = { confirmStop = true },
                        enabled = !actionBusy) { Text("Остановить", color = if (actionBusy) MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f) else MaterialTheme.colorScheme.error) }
                }
                if (actionBusy) LinearProgressIndicator(Modifier.fillMaxWidth())
            }
        }) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), state = listState,
            contentPadding = PaddingValues(UiSpace.screen), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                if (error.isNotBlank()) Surface(color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(18.dp)) {
                    Text("Не удалось обновить: $error", Modifier.padding(14.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer, style = MaterialTheme.typography.bodySmall)
                } else if (loading) CircularProgressIndicator(Modifier.padding(16.dp))
            }
            if (selectedId == null) {
                if (!loading && agents.isEmpty() && error.isBlank()) item {
                    Column {
                        Text("Пока нет субагентов", style = MaterialTheme.typography.titleMedium)
                        Text("Когда Codex поручит часть задачи агентам, они появятся здесь.",
                            Modifier.padding(top = 8.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                items(agents, key = { it.id }) { agent ->
                    Surface(onClick = { selectedId = agent.id }, shape = RoundedCornerShape(20.dp),
                        color = MaterialTheme.colorScheme.background) {
                        Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                UiGlyph(UiIcon.Agents, size = 21.dp)
                                Column(Modifier.weight(1f).padding(horizontal = 10.dp)) {
                                    Text(agent.name,
                                        fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    if (agent.model.isNotBlank()) Text(agent.model,
                                        Modifier.padding(top = 3.dp),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                                }
                                UiGlyph(UiIcon.ChevronRight, "Открыть чат агента", 20.dp)
                            }
                            Text(agentStatus(agent.status) + if (agent.role.isBlank()) "" else " · ${agent.role}",
                                Modifier.padding(top = 8.dp), style = MaterialTheme.typography.labelMedium,
                                color = if (agent.status == "running") MaterialTheme.colorScheme.secondary
                                    else MaterialTheme.colorScheme.onSurfaceVariant)
                            if (agent.task.isNotBlank()) Text(agent.task, Modifier.padding(top = 8.dp),
                                maxLines = 4, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                            if (agent.parentId != threadId) Text("Вложенный агент", Modifier.padding(top = 6.dp),
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            } else {
                item {
                    Column {
                    selected?.let { agent ->
                        if (agent.task.isNotBlank()) Text(agent.task, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    if (cursor != null) TextButton(enabled = !historyBusy, onClick = {
                        historyBusy = true
                        val requestedId = selectedId
                        val requestedCursor = cursor
                        scope.launch {
                            try {
                                val result = request("subagents/history?$rootQuery&agentId=${Uri.encode(requestedId)}&before=${Uri.encode(requestedCursor)}", null)
                                if (selectedId != requestedId) return@launch
                                val rows = result.optJSONArray("turns")
                                val older = (0 until (rows?.length() ?: 0)).mapNotNull { rows?.optJSONObject(it) }
                                history = (older + history).distinctBy { it.optString("id") }
                                cursor = result.optString("nextBefore").takeIf { result.optBoolean("hasMore") && it.isNotBlank() && it != "null" }
                            } catch (cancel: CancellationException) { throw cancel }
                            catch (failure: Exception) { notice = "История недоступна: ${failure.message}" }
                            finally { historyBusy = false }
                        }
                    }) { Text(if (historyBusy) "Загрузка…" else "Показать ранние сообщения") }
                    if (!historyReady && error.isBlank()) CircularProgressIndicator(Modifier.padding(16.dp))
                    }
                }
                items(history, key = { it.optString("id") }) { row ->
                    val role = row.optString("role")
                    Surface(shape = RoundedCornerShape(18.dp), color = if (role == "user")
                        MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.background) {
                        Column(Modifier.fillMaxWidth().padding(if (role == "user") 14.dp else 4.dp)) {
                            Text(when(role) { "user" -> "Задача / уточнение"; "process" -> "Ход работы"
                                "system" -> "Событие"; "outcome" -> "Итог работы"; else -> selected?.name ?: "Агент" },
                                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            SelectionContainer { MarkdownContent(row.optString("text"), compact = role == "system" || role == "process", onProjectFile = { onContextFile(it, selectedId.orEmpty()) }) }
                            val activities = row.optJSONArray("activities")
                            for (index in 0 until (activities?.length() ?: 0)) {
                                Text(activities?.optJSONObject(index)?.optString("label").orEmpty(),
                                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            val images = row.optJSONArray("images")
                            for (index in 0 until (images?.length() ?: 0)) {
                                images?.optJSONObject(index)?.let { image -> RemoteImage(
                                    "/api/chat/image?id=${Uri.encode(image.optString("id"))}", image.optString("name"),
                                    loadImage, Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 300.dp)
                                        .clickable(onClickLabel = "Открыть изображение") {
                                            openedImage = ChatImage(image.optString("id"), image.optString("name"))
                                        }, adaptivePreview = true) }
                            }
                        }
                    }
                }
            }
        }
    }
    openedImage?.let { image -> ImageViewer("/api/chat/image?id=${Uri.encode(image.id)}",
        image.name, loadImage, onClose = { openedImage = null }, onSave = { onSaveImage(image) }, saveStatus = saveStatus) }
    if (confirmStop) AlertDialog(onDismissRequest = { confirmStop = false },
        title = { Text("Остановить ${selected?.name ?: "агента"}?") },
        text = { Text("Текущая задача этого субагента будет прервана. Основной агент продолжит работу.") },
        confirmButton = { TextButton(onClick = { confirmStop = false; act("interrupt") }) { Text("Остановить") } },
        dismissButton = { TextButton(onClick = { confirmStop = false }) { Text("Отмена") } })
}
