package ru.wilmain.codexphone

import org.json.JSONObject

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import android.net.Uri
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.LocalDate

internal data class ActivityItem(val kind: String, val label: String, val status: String)
internal data class ChatLine(
    val role: String, val text: String, val turnId: String = "", val steps: Int = 0,
    val activities: List<ActivityItem> = emptyList(),
    val id: String = "", val time: String = "", val attachments: List<String> = emptyList(),
    val images: List<ChatImage> = emptyList(),
)
internal data class ChatImage(val id: String, val name: String)
internal data class PendingFile(val id: String, val name: String)
internal data class RemoteFile(
    val id: String, val name: String, val size: Long,
    val workspacePath: String = "", val threadId: String = "",
)
internal data class WorkspaceEntry(val name: String, val path: String, val isDirectory: Boolean, val size: Long)
internal data class LocalMessage(
    val id: String, val text: String, val files: List<String>,
    val threadId: String = "", val model: String? = null, val effort: String? = null,
    val acceptedByBridge: Boolean = false,
    val cancelRequested: Boolean = false,
    val deliveredTurnId: String = "",
    val steered: Boolean = false,
)
internal data class ThreadItem(val id: String, val title: String, val status: String, val updatedAt: Long)
internal data class ProjectGroup(val id: String, val name: String, val cwd: String?, val threads: List<ThreadItem>)
internal data class ModelOption(val id: String, val name: String, val efforts: List<String>, val defaultEffort: String)
internal data class LimitWindow(val remainingPercent: Int, val resetsAt: Long)
internal data class UsageLimits(val fiveHours: LimitWindow?, val week: LimitWindow?, val updatedAt: Long)

private fun effortLabel(value: String): String = when (value) {
    "none" -> "Без усилия"; "minimal" -> "Минимум"; "low" -> "Низкое"
    "medium" -> "Среднее"; "high" -> "Высокое"; "xhigh" -> "Очень высокое"
    "max" -> "Максимум"; "ultra" -> "Ультра"; else -> value
}

private fun actionCount(value: Int): String = when {
    value % 10 == 1 && value % 100 != 11 -> "$value действие"
    value % 10 in 2..4 && value % 100 !in 12..14 -> "$value действия"
    else -> "$value действий"
}

private fun activityGlyph(kind: String): UiIcon = when (kind) {
    "command" -> UiIcon.Terminal
    "file" -> UiIcon.File
    "image" -> UiIcon.Image
    "web" -> UiIcon.Globe
    else -> UiIcon.Circle
}

private fun timestamp(value: String): String = runCatching {
    val date = Instant.ofEpochSecond(value.toLong()).atZone(ZoneId.systemDefault())
    if (date.toLocalDate() == LocalDate.now()) date.format(DateTimeFormatter.ofPattern("HH:mm"))
    else date.format(DateTimeFormatter.ofPattern("dd.MM · HH:mm"))
}.getOrDefault("")

internal fun fileSize(size: Long): String = when {
    size < 1024 -> "$size Б"
    size < 1024 * 1024 -> "${size / 1024} КБ"
    else -> "${"%.1f".format(size / 1024.0 / 1024.0)} МБ"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CompanionUi(
    paired: Boolean,
    relayOnly: Boolean,
    themeMode: String,
    onThemeMode: (String) -> Unit,
    title: String,
    projectName: String,
    status: String,
    usageLimits: UsageLimits?, limitsLoading: Boolean, limitsError: String,
    lines: List<ChatLine>,
    queue: List<LocalMessage>,
    selectedThreadId: String,
    projects: List<ProjectGroup>,
    models: List<ModelOption>,
    selectedModel: String,
    selectedEffort: String,
    modelOverridden: Boolean,
    effortOverridden: Boolean,
    input: String,
    attachments: List<PendingFile>,
    outboxFiles: List<RemoteFile>,
    outboxLoading: Boolean,
    outboxError: String,
    workspaceRoot: String,
    workspacePath: String,
    workspaceEntries: List<WorkspaceEntry>,
    workspaceLoading: Boolean,
    workspaceError: String,
    workspaceTruncated: Boolean,
    previewPath: String,
    previewText: String,
    previewNote: String,
    previewLoading: Boolean,
    fileStatus: String,
    transferringFileId: String,
    transferProgress: Pair<Long, Long>?,
    historyHasMore: Boolean,
    historyLoading: Boolean,
    historyError: String,
    onInput: (String) -> Unit,
    onScan: () -> Unit,
    onSelectThread: (String) -> Unit,
    onDeleteThread: (String) -> Unit,
    onMoveThread: (String, String) -> Unit,
    onNewChat: (String?) -> Unit,
    onRefreshCatalog: () -> Unit,
    onRefreshLimits: () -> Unit,
    onAdbRequest: suspend (String, JSONObject?) -> JSONObject,
    onModel: (String) -> Unit,
    onEffort: (String) -> Unit,
    onAttach: () -> Unit,
    onRemoveAttachment: (PendingFile) -> Unit,
    onFetchFiles: () -> Unit,
    onSaveFile: (RemoteFile) -> Unit,
    onBrowseWorkspace: (String) -> Unit,
    onPreviewWorkspace: (String) -> Unit,
    onAskWorkspace: (String) -> Unit,
    onSaveWorkspace: (WorkspaceEntry) -> Unit,
    onResolveProjectFile: suspend (String) -> String?,
    onSaveChatImage: (ChatImage) -> Unit,
    onLoadOlder: () -> Unit,
    onSend: () -> Unit,
    onCancelQueued: (LocalMessage) -> Unit,
    onSteerQueued: (LocalMessage) -> Unit,
    onCancelTransfer: () -> Unit,
    onDisconnect: () -> Unit,
    loadImage: suspend (String) -> ByteArray,
) {
    val clipboard = LocalClipboardManager.current
    val density = LocalDensity.current
    val imeHeight = WindowInsets.ime.getBottom(density)
    val listState = remember(selectedThreadId) { LazyListState() }
    var firstHistoryScroll by remember(selectedThreadId) { mutableStateOf(true) }
    var modelSheet by remember { mutableStateOf(false) }
    var draftModel by remember { mutableStateOf("") }
    var draftEffort by remember { mutableStateOf("") }
    var filesOpen by remember { mutableStateOf(false) }
    var projectsOpen by remember { mutableStateOf(false) }
    var devicesOpen by remember { mutableStateOf(false) }
    var openedImage by remember { mutableStateOf<ChatImage?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(projectsOpen) {
        while (projectsOpen) {
            onRefreshLimits()
            delay(60_000)
        }
    }
    var disconnectDialog by remember { mutableStateOf(false) }
    val expandedProcesses = remember { mutableStateMapOf<String, Boolean>() }
    val visibleQueue = queue.filter { it.threadId.isBlank() || it.threadId == selectedThreadId }
    val visibleLines = lines
    val selectedProjectName = projects.firstOrNull { group ->
        group.id != "other" && group.threads.any { it.id == selectedThreadId }
    }?.name.orEmpty()
    LaunchedEffect(selectedThreadId, visibleLines.lastOrNull()?.id, visibleQueue.size) {
        val total = visibleLines.size + visibleQueue.size + if (historyHasMore || historyLoading || historyError.isNotBlank()) 1 else 0
        if (total > 0) {
            val last = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index
            if (firstHistoryScroll || (last != null && last >= total - 3) ||
                (total <= 3 && listState.firstVisibleItemIndex == 0)) {
                listState.animateScrollToItem(total - 1)
                firstHistoryScroll = false
            }
        }
    }
    val viewportHeight = listState.layoutInfo.viewportEndOffset - listState.layoutInfo.viewportStartOffset
    LaunchedEffect(selectedThreadId, imeHeight, viewportHeight) {
        if (imeHeight > 0 && listState.layoutInfo.totalItemsCount > 0) {
            withFrameNanos { }
            listState.scrollToItem(listState.layoutInfo.totalItemsCount - 1)
            listState.scroll { scrollBy(100_000f) }
        }
    }
    val modelName = models.firstOrNull { it.id == selectedModel }?.name ?: selectedModel.ifBlank { "Модель чата" }
    if (filesOpen && paired) {
        WorkspaceFilesScreen(
            projectName = projectName, rootName = workspaceRoot, threadId = selectedThreadId,
            path = workspacePath, entries = workspaceEntries,
            loading = workspaceLoading, error = workspaceError, truncated = workspaceTruncated,
            previewPath = previewPath, previewText = previewText, previewNote = previewNote,
            previewLoading = previewLoading,
            fileStatus = fileStatus,
            outboxFiles = outboxFiles, outboxLoading = outboxLoading, outboxError = outboxError,
            transferringFileId = transferringFileId, transferProgress = transferProgress,
            pendingFiles = attachments,
            onCancelTransfer = onCancelTransfer,
            loadImage = loadImage,
            onClose = { filesOpen = false }, onBrowse = onBrowseWorkspace,
            onPreview = onPreviewWorkspace,
            onAsk = { path -> onAskWorkspace(path); filesOpen = false },
            onSaveWorkspace = onSaveWorkspace, onSaveOutbox = onSaveFile,
            onFetchOutbox = onFetchFiles, onAttach = onAttach,
        )
        return
    }
    if (devicesOpen && paired) {
        AdbDevicesScreen(onClose = { devicesOpen = false }, onRequest = onAdbRequest)
        return
    }
    if (projectsOpen && paired) {
        ProjectsScreen(
            projects = projects, selectedThreadId = selectedThreadId,
            themeMode = themeMode,
            usageLimits = usageLimits, limitsLoading = limitsLoading, limitsError = limitsError,
            onThemeMode = onThemeMode, onClose = { projectsOpen = false },
            onSelectThread = { projectsOpen = false; onSelectThread(it) },
            onDeleteThread = onDeleteThread,
            onMoveThread = onMoveThread,
            actionError = if (status.startsWith("Не удалось переместить") ||
                status.startsWith("Не удалось удалить")) status else "",
            onNewChat = { projectsOpen = false; onNewChat(it) },
            onRefreshCatalog = onRefreshCatalog,
            onRefreshLimits = onRefreshLimits,
            onDevices = { projectsOpen = false; devicesOpen = true },
            onDisconnect = { disconnectDialog = true },
        )
        if (disconnectDialog) AlertDialog(
            onDismissRequest = { disconnectDialog = false },
            title = { Text("Отключить телефон?") },
            text = { Text("Привязка к этому ПК будет удалена. Для повторного подключения понадобится QR-код.") },
            confirmButton = { TextButton(onClick = { disconnectDialog = false; projectsOpen = false; onDisconnect() }) { Text("Отключить") } },
            dismissButton = { TextButton(onClick = { disconnectDialog = false }) { Text("Отмена") } },
        )
        return
    }
    Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text(if (paired) title else "RemoteVibecode", maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                            if (paired) Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(Modifier.size(7.dp).background(
                                    if (status.startsWith("Нет связи")) MaterialTheme.colorScheme.error
                                    else MaterialTheme.colorScheme.secondary, CircleShape))
                                Spacer(Modifier.width(5.dp))
                                Text("${selectedProjectName.ifBlank { "Без проекта" }} · $status",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    style = MaterialTheme.typography.labelSmall,
                                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    },
                    navigationIcon = { if (paired) TextButton(onClick = {
                        onRefreshCatalog(); projectsOpen = true
                    }, modifier = Modifier.size(48.dp).semantics { contentDescription = "Проекты и чаты" }) {
                        UiGlyph(UiIcon.Menu, size = 22.dp)
                    } },
                    actions = { if (paired) {
                        TextButton(onClick = { filesOpen = true; onBrowseWorkspace(""); onFetchFiles() },
                            modifier = Modifier.size(48.dp).semantics { contentDescription = "Файлы проекта" }) {
                            UiGlyph(UiIcon.Files, size = 22.dp)
                        }
                        TextButton(onClick = { onNewChat(null) },
                            modifier = Modifier.size(48.dp).semantics { contentDescription = "Новый чат" }) {
                            UiGlyph(UiIcon.Plus, size = 23.dp)
                        }
                    } },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                )
            },
            bottomBar = {
                if (paired) Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background).imePadding().navigationBarsPadding()
                    .padding(horizontal = 14.dp, vertical = 8.dp)) {
                    if (visibleQueue.isNotEmpty()) {
                        val next = visibleQueue.first()
                        Surface(shape = RoundedCornerShape(15.dp),
                            color = MaterialTheme.colorScheme.secondaryContainer,
                            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
                            Row(Modifier.fillMaxWidth().padding(start = 13.dp, end = 7.dp, top = 5.dp, bottom = 5.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text("В очереди · ${visibleQueue.size}",
                                        style = MaterialTheme.typography.labelMedium,
                                        fontWeight = FontWeight.SemiBold)
                                    Text(next.text.ifBlank { "Сообщение с вложением" }, maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                        style = MaterialTheme.typography.bodySmall)
                                }
                                if (next.acceptedByBridge && !next.cancelRequested)
                                    TextButton(onClick = { onSteerQueued(next) }) { Text("Корректировать") }
                            }
                        }
                    }
                    Surface(shape = RoundedCornerShape(22.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant) {
                        Column(Modifier.padding(horizontal = 8.dp, vertical = 5.dp)) {
                            BasicTextField(input, onInput,
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 10.dp),
                                textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
                                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                                minLines = 1, maxLines = 5,
                                decorationBox = { innerField ->
                                    Box {
                                        if (input.isEmpty()) Text("Сообщение для Codex",
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            style = MaterialTheme.typography.bodyLarge)
                                        innerField()
                                    }
                                })
                            attachments.forEach { file ->
                                Row(Modifier.fillMaxWidth().padding(start = 8.dp, bottom = 3.dp),
                                    verticalAlignment = Alignment.CenterVertically) {
                                    Text(file.name, modifier = Modifier.weight(1f), maxLines = 1,
                                        overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelMedium)
                                    TextButton(onClick = { onRemoveAttachment(file) },
                                        modifier = Modifier.semantics { contentDescription = "Убрать вложение ${file.name}" }) {
                                        UiGlyph(UiIcon.Close)
                                    }
                                }
                            }
                            if (transferringFileId.isNotBlank()) TextButton(onClick = onCancelTransfer) {
                                Text("Отменить передачу")
                            }
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(1.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                TextButton(onClick = onAttach, modifier = Modifier.size(48.dp)
                                    .semantics { contentDescription = "Прикрепить файл" }) {
                                    UiGlyph(UiIcon.Plus, size = 23.dp)
                                }
                                TextButton(onClick = { filesOpen = true; onBrowseWorkspace(""); onFetchFiles() },
                                    modifier = Modifier.size(48.dp).semantics { contentDescription = "Файлы проекта" }) {
                                    UiGlyph(UiIcon.Files, size = 21.dp)
                                }
                                TextButton(onClick = {
                                    draftModel = if (modelOverridden) selectedModel else ""
                                    draftEffort = if (effortOverridden) selectedEffort else ""
                                    modelSheet = true
                                }, modifier = Modifier.weight(1f).height(48.dp),
                                    contentPadding = PaddingValues(horizontal = 2.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text("$modelName · ${effortLabel(selectedEffort.ifBlank { "по умолчанию" })}",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        Spacer(Modifier.width(3.dp))
                                        UiGlyph(UiIcon.ChevronDown, size = 14.dp,
                                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                                Button(onClick = onSend, enabled = input.isNotBlank() || attachments.isNotEmpty(),
                                    shape = CircleShape, contentPadding = PaddingValues(0.dp),
                                    modifier = Modifier.size(48.dp).semantics { contentDescription = "Отправить сообщение" }) {
                                    UiGlyph(UiIcon.ArrowUp, size = 22.dp)
                                }
                            }
                        }
                    }
                }
            },
        ) { inner ->
            if (!paired) {
                Column(Modifier.fillMaxSize().padding(inner).padding(24.dp),
                    verticalArrangement = Arrangement.Center) {
                    Text(if (relayOnly) "RemoteVibecode" else "RemoteVibecode Classic", style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(12.dp))
                    Text(if (relayOnly)
                        "Откройте агент RemoteVibecode на ПК и отсканируйте его QR-код. Телефон подключится через ваш ретранслятор; адрес сервера и отпечаток сертификата уже включены в код."
                        else "Один раз подключите телефон к вашему ПК. Затем чаты и файлы будут доступны сразу.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(24.dp))
                    Button(onClick = onScan, modifier = Modifier.fillMaxWidth().height(52.dp)) { Text("Сканировать QR-код") }
                    Spacer(Modifier.height(14.dp))
                    Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall)
                }
            } else {
                Box(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner)) {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(start = 18.dp, end = 18.dp,
                        top = 12.dp, bottom = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    if (historyHasMore || historyLoading || historyError.isNotBlank()) item(key = "older-history") {
                        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                            if (historyError.isNotBlank()) Text(historyError,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall)
                            TextButton(onClick = onLoadOlder, enabled = !historyLoading) {
                                Text(if (historyLoading) "Загружаю…" else "Показать ранние сообщения")
                            }
                        }
                    }
                    if (visibleLines.isEmpty() && visibleQueue.isEmpty()) item {
                        Box(Modifier.fillMaxWidth().padding(top = 80.dp), contentAlignment = Alignment.Center) {
                            Text("Напишите сообщение для Codex", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    itemsIndexed(visibleLines, key = { index, line -> line.id.ifBlank { "${line.turnId}:$index" } }) { _, line ->
                        when (line.role) {
                            "user" -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
                                Surface(shape = RoundedCornerShape(20.dp, 20.dp, 6.dp, 20.dp),
                                    color = MaterialTheme.colorScheme.surfaceVariant,
                                    modifier = Modifier.fillMaxWidth(0.88f)) {
                                    Column(Modifier.padding(14.dp)) {
                                        if (line.text.isNotBlank()) SelectionContainer {
                                            MarkdownContent(line.text, onProjectFile = { reference ->
                                                scope.launch {
                                                    onResolveProjectFile(reference)?.let { folder ->
                                                        onBrowseWorkspace(folder)
                                                        filesOpen = true
                                                    }
                                                }
                                            })
                                        }
                                        line.attachments.forEach { name ->
                                            Surface(shape = RoundedCornerShape(10.dp),
                                                color = MaterialTheme.colorScheme.background,
                                                modifier = Modifier.padding(top = 9.dp)) {
                                                Row(Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                                                    verticalAlignment = Alignment.CenterVertically) {
                                                    UiGlyph(UiIcon.File, size = 16.dp,
                                                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                                    Spacer(Modifier.width(7.dp))
                                                    Text(name, style = MaterialTheme.typography.labelMedium,
                                                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                                                }
                                            }
                                        }
                                        line.images.forEach { image ->
                                            RemoteImage("/api/chat/image?id=${Uri.encode(image.id)}", image.name,
                                                loadImage, Modifier.fillMaxWidth().height(260.dp)
                                                    .padding(top = 9.dp).clickable { openedImage = image })
                                        }
                                    }
                                }
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    if (line.time.isNotBlank()) Text(timestamp(line.time),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        style = MaterialTheme.typography.labelSmall)
                                    TextButton(onClick = { clipboard.setText(AnnotatedString(line.text)) }) { Text("Копировать") }
                                }
                            }
                            "process" -> {
                                val expanded = expandedProcesses[line.turnId] ?: false
                                Surface(shape = RoundedCornerShape(12.dp),
                                    color = MaterialTheme.colorScheme.surfaceVariant,
                                    modifier = Modifier.fillMaxWidth().clickable { expandedProcesses[line.turnId] = !expanded }) {
                                    Column(Modifier.padding(horizontal = 13.dp, vertical = 10.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            UiGlyph(if (expanded) UiIcon.ChevronDown else UiIcon.ChevronRight,
                                                size = 16.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                            Spacer(Modifier.width(7.dp))
                                            Text("Ход работы" +
                                                if (line.steps > 0) " · ${actionCount(line.steps)}" else "",
                                                style = MaterialTheme.typography.labelMedium,
                                                fontWeight = FontWeight.SemiBold,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                        if (expanded) {
                                            if (line.text.isNotBlank()) {
                                                Spacer(Modifier.height(10.dp))
                                                SelectionContainer { Text(line.text,
                                                    style = MaterialTheme.typography.bodySmall,
                                                    color = MaterialTheme.colorScheme.onSurfaceVariant) }
                                            }
                                        }
                                        if (line.steps > line.activities.size && expanded) {
                                            Text("Последние ${line.activities.size} из ${line.steps} действий",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                                modifier = Modifier.padding(top = 8.dp))
                                        }
                                        (if (expanded) line.activities else line.activities.takeLast(3)).forEach { activity ->
                                                Row(Modifier.fillMaxWidth().padding(top = 9.dp),
                                                    verticalAlignment = Alignment.Top) {
                                                    Box(Modifier.width(22.dp)) {
                                                        UiGlyph(if (activity.status == "completed") activityGlyph(activity.kind)
                                                            else UiIcon.Loader, size = 16.dp,
                                                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                                    }
                                                    Text(activity.label, modifier = Modifier.clearAndSetSemantics {
                                                        contentDescription = activity.label + if (activity.status == "completed") ", завершено" else ", выполняется"
                                                    }, style = MaterialTheme.typography.bodySmall,
                                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                                }
                                        }
                                    }
                                }
                            }
                            else -> Column {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Surface(shape = RoundedCornerShape(8.dp),
                                        color = MaterialTheme.colorScheme.surfaceVariant) {
                                        Box(Modifier.size(27.dp), contentAlignment = Alignment.Center) {
                                            UiGlyph(UiIcon.Sparkles, size = 16.dp)
                                        }
                                    }
                                    Spacer(Modifier.width(8.dp))
                                    Text("Codex", style = MaterialTheme.typography.labelMedium,
                                        fontWeight = FontWeight.SemiBold)
                                    if (line.time.isNotBlank()) {
                                        Spacer(Modifier.width(8.dp))
                                        Text(timestamp(line.time), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            style = MaterialTheme.typography.labelSmall)
                                    }
                                }
                                Spacer(Modifier.height(8.dp))
                                SelectionContainer {
                                    MarkdownContent(line.text, onProjectFile = { reference ->
                                        scope.launch {
                                            onResolveProjectFile(reference)?.let { folder ->
                                                onBrowseWorkspace(folder)
                                                filesOpen = true
                                            }
                                        }
                                    })
                                }
                                line.images.forEach { image ->
                                    RemoteImage("/api/chat/image?id=${Uri.encode(image.id)}", image.name,
                                        loadImage, Modifier.fillMaxWidth().height(260.dp)
                                            .padding(top = 8.dp).clickable { openedImage = image })
                                }
                                TextButton(onClick = { clipboard.setText(AnnotatedString(line.text)) },
                                    contentPadding = PaddingValues(horizontal = 0.dp, vertical = 0.dp)) {
                                    Text("Копировать", style = MaterialTheme.typography.labelSmall)
                                }
                            }
                        }
                    }
                    items(visibleQueue, key = { "queue:${it.id}" }) { item ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                            Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.secondaryContainer,
                                modifier = Modifier.fillMaxWidth(0.86f)) {
                                Column(Modifier.padding(13.dp)) {
                                    Text(if (item.deliveredTurnId.isNotBlank())
                                        (if (item.steered) "КОРРЕКТИРОВКА ОТПРАВЛЕНА" else "ОТПРАВЛЕНО")
                                        else "В ОЧЕРЕДИ", style = MaterialTheme.typography.labelSmall,
                                        fontWeight = FontWeight.Bold,
                                        color = MaterialTheme.colorScheme.onSecondaryContainer)
                                    Spacer(Modifier.height(5.dp))
                                    Text(item.text.ifBlank { "Вложение · ${item.files.size}" },
                                        style = MaterialTheme.typography.bodyLarge)
                                    Spacer(Modifier.height(4.dp))
                                    Text(if (item.deliveredTurnId.isNotBlank()) "Ожидаю отображения в истории"
                                        else if (item.cancelRequested) "Отменяю…" else if (item.acceptedByBridge)
                                        "В очереди Codex" else "Ожидает сети",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    Row {
                                        if (item.acceptedByBridge && !item.cancelRequested && item.deliveredTurnId.isBlank())
                                            TextButton(onClick = { onSteerQueued(item) }) {
                                                Text("Корректировать сейчас")
                                            }
                                        if (item.deliveredTurnId.isBlank()) TextButton(onClick = { onCancelQueued(item) }, enabled = !item.cancelRequested) {
                                            Text("Убрать")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if (listState.canScrollForward) {
                    Surface(
                        modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 16.dp)
                            .size(48.dp).semantics { contentDescription = "К последнему сообщению" }
                            .clickable {
                                scope.launch {
                                    val last = listState.layoutInfo.totalItemsCount - 1
                                    if (last >= 0) listState.animateScrollToItem(last)
                                }
                            },
                        shape = CircleShape,
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        shadowElevation = 5.dp,
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            UiGlyph(UiIcon.ChevronDown, size = 22.dp)
                        }
                    }
                }
            }
        }
    }
    openedImage?.let { image ->
        Dialog(onDismissRequest = { openedImage = null },
            properties = DialogProperties(usePlatformDefaultWidth = false)) {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                Column(Modifier.fillMaxSize().navigationBarsPadding().padding(16.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        TextButton(onClick = { openedImage = null }) { Text("Закрыть") }
                        Text(image.name, Modifier.weight(1f), maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.titleSmall)
                        TextButton(onClick = { onSaveChatImage(image) }) {
                            UiGlyph(UiIcon.Download, size = 18.dp)
                            Spacer(Modifier.width(5.dp))
                            Text("Скачать")
                        }
                    }
                    RemoteImage("/api/chat/image?id=${Uri.encode(image.id)}", image.name,
                        loadImage, Modifier.fillMaxWidth().weight(1f))
                }
            }
        }
    }
    if (modelSheet) ModalBottomSheet(onDismissRequest = { modelSheet = false }) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().verticalScroll(rememberScrollState())
            .padding(start = 18.dp, end = 18.dp, bottom = 36.dp)) {
            Text("Модель и усилие", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(14.dp))
            Text("МОДЕЛЬ", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth().semantics { selected = draftModel.isBlank() }
                .clickable { draftModel = ""; draftEffort = "" }
                .padding(horizontal = 8.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Как в чате", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                if (draftModel.isBlank()) UiGlyph(UiIcon.Check, size = 17.dp)
            }
            LazyColumn(Modifier.height(108.dp)) {
                items(models) { model ->
                    Row(Modifier.fillMaxWidth().semantics { selected = model.id == draftModel }
                        .clickable { draftModel = model.id; draftEffort = "" }
                        .padding(horizontal = 8.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(model.name, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                            fontWeight = if (model.id == draftModel) FontWeight.SemiBold else FontWeight.Normal)
                        if (model.id == draftModel) UiGlyph(UiIcon.Check, size = 17.dp)
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Text("УСИЛИЕ", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            val effortOptions = models.firstOrNull { it.id == draftModel.ifBlank { selectedModel } }?.efforts ?: emptyList()
            androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Surface(shape = RoundedCornerShape(18.dp),
                    color = if (draftEffort.isBlank()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.semantics { selected = draftEffort.isBlank() }
                        .clickable { draftEffort = "" }) {
                    Text("Как в чате", modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (draftEffort.isBlank()) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
                }
                effortOptions.forEach { effort ->
                    Surface(shape = RoundedCornerShape(18.dp),
                        color = if (effort == draftEffort) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant,
                        modifier = Modifier.semantics { selected = draftEffort == effort }
                            .clickable { draftEffort = effort }) {
                        Text(effortLabel(effort), modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                            style = MaterialTheme.typography.labelMedium,
                            color = if (effort == draftEffort) MaterialTheme.colorScheme.onPrimary
                            else MaterialTheme.colorScheme.onSurface)
                    }
                }
            }
            Spacer(Modifier.height(18.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = { modelSheet = false }) { Text("Отмена") }
                Button(onClick = {
                    onModel(draftModel)
                    onEffort(draftEffort)
                    modelSheet = false
                }) { Text("Применить") }
            }
        }
    }
    if (disconnectDialog) AlertDialog(
        onDismissRequest = { disconnectDialog = false },
        title = { Text("Отключить телефон?") },
        text = { Text("Привязка к этому ПК будет удалена. Для повторного подключения понадобится QR-код.") },
        confirmButton = { TextButton(onClick = { disconnectDialog = false; onDisconnect() }) { Text("Отключить") } },
        dismissButton = { TextButton(onClick = { disconnectDialog = false }) { Text("Отмена") } },
    )
}
