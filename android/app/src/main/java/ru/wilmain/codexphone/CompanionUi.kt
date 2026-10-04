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
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
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
import androidx.compose.material3.IconButton
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.ui.semantics.heading
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
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.snapshotFlow
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import android.net.Uri
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
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
    val outcomeSummary: String = "", val quotaSummary: String = "",
    val clientMessageId: String = "",
)
internal data class ChatImage(val id: String, val name: String)
internal data class PendingFile(val id: String, val name: String)
internal data class RemoteFile(
    val id: String, val name: String, val size: Long,
    val workspacePath: String = "", val threadId: String = "",
)
internal data class WorkspaceEntry(val name: String, val path: String, val isDirectory: Boolean, val size: Long, val available: Boolean = true, val blockedReason: String = "", val isLink: Boolean = false)
internal data class LocalMessage(
    val id: String, val text: String, val files: List<String>,
    val threadId: String = "", val model: String? = null, val effort: String? = null,
    val acceptedByBridge: Boolean = false,
    val cancelRequested: Boolean = false,
    val deliveredTurnId: String = "",
    val steered: Boolean = false,
    val nativeSubmissionId: String = "",
    val queueState: String = "queued",
)
internal data class ThreadItem(val id: String, val title: String, val status: String, val updatedAt: Long)
internal data class ProjectGroup(val id: String, val name: String, val cwd: String?, val threads: List<ThreadItem>)
internal data class ModelOption(val id: String, val name: String, val efforts: List<String>, val defaultEffort: String)
internal data class LimitWindow(val remainingPercent: Int, val resetsAt: Long)
internal data class UsageLimits(val fiveHours: LimitWindow?, val week: LimitWindow?, val updatedAt: Long, val resetCredits: Int? = null)

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
    onUpdates: () -> Unit,
    updateAvailable: Boolean,
    paired: Boolean,
    relayOnly: Boolean,
    themeMode: String,
    onThemeMode: (String) -> Unit,
    title: String,
    projectName: String,
    status: String,
    connectionState: ConnectionState,
    usageLimits: UsageLimits?, limitsLoading: Boolean, limitsError: String,
    lines: List<ChatLine>,
    queue: List<LocalMessage>,
    selectedThreadId: String,
    projects: List<ProjectGroup>,
    catalogLoading: Boolean, catalogError: String,
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
    initialHistoryLoading: Boolean, initialHistoryError: String,
    onRetryHistory: () -> Unit,
    onInput: (String) -> Unit,
    onScan: () -> Unit,
    onSelectThread: (String) -> Unit,
    onDeleteThread: (String) -> Unit,
    onMoveThread: (String, String) -> Unit,
    onNewChat: (String?) -> Unit,
    onRefreshCatalog: () -> Unit,
    onRefreshLimits: () -> Unit,
    onResetLimits: (String) -> Unit,
    resetMessage: String,
    resetLoading: Boolean,
    resetPending: Boolean,
    onSubagentRequest: suspend (String, JSONObject?) -> JSONObject,
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
    activeTurnId: String = "",
    taskNotifications: Boolean = false,
    onTaskNotifications: () -> Unit = {},
    onJumpHistory: (String) -> Unit = {},
    workspaceNextCursor: String = "",
    onWorkspaceQuery: ((String, String, Boolean, Boolean, String) -> Unit)? = null,
    compatibilityNote: String = "",
    workspaceThreadId: String = selectedThreadId,
    onResolveContextFile: suspend (String, String) -> String? = { reference, _ -> onResolveProjectFile(reference) },
) {
    var dismissUnknown by remember { mutableStateOf<LocalMessage?>(null) }
    dismissUnknown?.let { message ->
        AlertDialog(onDismissRequest = { dismissUnknown = null },
            title = { Text("Скрыть уведомление?") },
            text = { Text("Desktop не подтвердил доставку или отмену. Это действие уберёт только уведомление телефона; задача на ПК может продолжаться. Повторная отправка с прежним ID запрещена.") },
            confirmButton = { TextButton(onClick = { onCancelQueued(message); dismissUnknown = null }) { Text("Скрыть уведомление") } },
            dismissButton = { TextButton(onClick = { dismissUnknown = null }) { Text("Назад") } })
    }
    var controlsOpen by rememberSaveable(selectedThreadId) { mutableStateOf(false) }
    var controlsTurn by rememberSaveable(selectedThreadId) { mutableStateOf("") }
    var focusTurn by rememberSaveable(selectedThreadId) { mutableStateOf("") }
    var changesOpen by rememberSaveable(selectedThreadId) { mutableStateOf(false) }
    val clipboard = LocalClipboardManager.current
    val density = LocalDensity.current
    val imeHeight = WindowInsets.ime.getBottom(density)
    val listState = rememberSaveable(selectedThreadId, saver = LazyListState.Saver) { LazyListState() }
    var firstHistoryScroll by rememberSaveable(selectedThreadId) { mutableStateOf(true) }
    var followBottom by rememberSaveable(selectedThreadId) { mutableStateOf(true) }
    var scrollingToBottom by remember(selectedThreadId) { mutableStateOf(false) }
    val historyScrollConnection = remember(listState) {
        object : NestedScrollConnection {
            override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset {
                if (source == NestedScrollSource.UserInput && available.y != 0f) {
                    // Record user intent synchronously; snapshotFlow can conflate a
                    // short gesture with its final idle state, or an interrupted auto-scroll.
                    firstHistoryScroll = false
                    followBottom = false
                }
                return Offset.Zero
            }

            override fun onPostScroll(consumed: Offset, available: Offset, source: NestedScrollSource): Offset {
                if (source == NestedScrollSource.UserInput && (consumed.y != 0f || available.y != 0f)) {
                    followBottom = !listState.canScrollForward
                }
                return Offset.Zero
            }
        }
    }
    val composerContentHeight = (LocalConfiguration.current.screenHeightDp.dp -
        with(density) { imeHeight.toDp() } - 220.dp).coerceIn(72.dp, 220.dp)
    var modelSheet by rememberSaveable { mutableStateOf(false) }
    var draftModel by rememberSaveable { mutableStateOf("") }
    var draftEffort by rememberSaveable { mutableStateOf("") }
    var subagentsOpen by rememberSaveable { mutableStateOf(false) }
    var filesOpen by rememberSaveable { mutableStateOf(false) }
    var projectsOpen by rememberSaveable { mutableStateOf(false) }
    var devicesOpen by rememberSaveable { mutableStateOf(false) }
    var openedImage by rememberSaveable(stateSaver = ChatImageSaver) { mutableStateOf<ChatImage?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(projectsOpen) {
        while (projectsOpen) {
            onRefreshLimits()
            delay(60_000)
        }
    }
    var disconnectDialog by remember { mutableStateOf(false) }
    val expandedProcesses = rememberExpansionState()
    var chatMenuOpen by rememberSaveable { mutableStateOf(false) }
    val visibleQueue = queue.filter { it.threadId.isBlank() || it.threadId == selectedThreadId }
    val visibleLines = lines
    val selectedProjectName = projects.firstOrNull { group ->
        group.id != "other" && group.threads.any { it.id == selectedThreadId }
    }?.name.orEmpty()
    suspend fun scrollToEnd() {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last < 0) return
        scrollingToBottom = true
        try {
            listState.scrollToItem(last)
            listState.scroll { scrollBy(100_000f) }
        } finally {
            scrollingToBottom = false
        }
    }
    LaunchedEffect(listState) {
        var userScrolling = false
        snapshotFlow { Triple(listState.isScrollInProgress, scrollingToBottom, listState.canScrollForward) }
            .collect { (scrolling, automatic, canScrollForward) ->
                if (scrolling && !automatic) userScrolling = true
                if (userScrolling) {
                    firstHistoryScroll = false
                    followBottom = !canScrollForward
                    if (!scrolling) userScrolling = false
                }
            }
    }
    val viewportHeight by remember(listState) { derivedStateOf {
        listState.layoutInfo.viewportEndOffset - listState.layoutInfo.viewportStartOffset
    } }
    LaunchedEffect(selectedThreadId, visibleLines.lastOrNull()?.id, visibleLines.lastOrNull()?.text,
        visibleQueue.size, imeHeight, viewportHeight) {
        // Stable item keys keep the first visible item and offset while reading history.
        // Follow viewport/IME changes only if the user was following the end.
        if (visibleLines.isNotEmpty() || visibleQueue.isNotEmpty()) {
            if ((firstHistoryScroll || followBottom) && !listState.isScrollInProgress) {
                // An interrupted initial scroll must never re-arm after the user scrolls away.
                firstHistoryScroll = false
                withFrameNanos { }
                if (!followBottom || listState.isScrollInProgress) return@LaunchedEffect
                scrollToEnd()
            }
        }
    }
    val modelName = models.firstOrNull { it.id == selectedModel }?.name ?: selectedModel.ifBlank { "Модель чата" }
    LaunchedEffect(focusTurn, lines) {
        if (focusTurn.isNotBlank()) {
            val index = lines.indexOfFirst { it.turnId == focusTurn && it.role == "user" }
            if (index >= 0) {
                firstHistoryScroll = false; followBottom = false
                listState.scrollToItem(index + if (historyHasMore || historyLoading || historyError.isNotBlank()) 1 else 0)
                focusTurn = ""
            }
        }
    }
    if (controlsOpen && paired) {
        WorkspaceControls(selectedThreadId, title, controlsTurn, onSubagentRequest,
            onClose = { controlsOpen = false },
            onSelect = { controlsOpen = false; onSelectThread(it) },
            onJump = { focusTurn = it; onJumpHistory(it) },
            onChanged = { onRefreshCatalog(); onRetryHistory() }, compatibilityNote=compatibilityNote,
            onProjectFile = { reference -> scope.launch { onResolveProjectFile(reference)?.let { folder ->
                onBrowseWorkspace(folder); controlsOpen = false; filesOpen = true
            } } })
        return
    }
    if (changesOpen && paired) {
        ChangesScreen(selectedThreadId, onSubagentRequest, { changesOpen = false }) { reference ->
            scope.launch { onResolveProjectFile(reference)?.let { folder ->
                onBrowseWorkspace(folder); changesOpen = false; filesOpen = true
            } }
        }
        return
    }
    if (subagentsOpen && paired) {
        SubagentsScreen(selectedThreadId, { subagentsOpen = false }, onSubagentRequest, loadImage,
            onProjectFile = { reference -> scope.launch {
                onResolveProjectFile(reference)?.let { folder ->
                    onBrowseWorkspace(folder); subagentsOpen = false; filesOpen = true
                }
            } }, onSaveImage = onSaveChatImage, saveStatus = status,
            onContextFile = { reference, source -> scope.launch {
                onResolveContextFile(reference, source)?.let { folder ->
                    onBrowseWorkspace(folder); subagentsOpen = false; filesOpen = true
                }
            } })
        return
    }
    if (filesOpen && paired) {
        WorkspaceFilesScreen(
            projectName = projectName, rootName = workspaceRoot, threadId = workspaceThreadId,
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
            nextCursor = workspaceNextCursor, onListing = onWorkspaceQuery,
            onClose = { filesOpen = false }, onBrowse = onBrowseWorkspace,
            onPreview = onPreviewWorkspace,
            onAsk = { path -> onAskWorkspace(path); filesOpen = false },
            onSaveWorkspace = onSaveWorkspace, onSaveOutbox = onSaveFile,
            onFetchOutbox = onFetchFiles, onAttach = onAttach, saveStatus = status,
            onProjectFile = { reference -> scope.launch {
                onResolveContextFile(reference, workspaceThreadId)?.let { folder -> onPreviewWorkspace(""); onBrowseWorkspace(folder) }
            } },
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
            catalogLoading = catalogLoading, catalogError = catalogError,
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
            onResetLimits = onResetLimits, resetMessage = resetMessage, resetLoading = resetLoading, resetPending = resetPending,
            onUpdates = onUpdates, updateAvailable = updateAvailable,
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
                Row(Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    if (paired) IconButton(onClick = { onRefreshCatalog(); projectsOpen = true },
                        modifier = Modifier.size(48.dp).semantics { contentDescription = "Проекты и чаты" }) {
                        UiGlyph(UiIcon.Menu, size = 24.dp)
                    }
                    Column(Modifier.weight(1f).padding(horizontal = 8.dp)) {
                        Text(if (paired) title else "RemoteVibecode", maxLines = 2,
                            modifier = Modifier.semantics { heading() }, overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                        if (paired) Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(7.dp).background(when (connectionState) {
                                ConnectionState.Online -> MaterialTheme.colorScheme.secondary
                                ConnectionState.Reconnecting -> MaterialTheme.colorScheme.error
                                else -> MaterialTheme.colorScheme.onSurfaceVariant
                            }, CircleShape))
                            Spacer(Modifier.width(6.dp))
                            Text("${selectedProjectName.ifBlank { projectName.ifBlank { "Без проекта" } }} · $status",
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    Box {
                        IconButton(onClick = { chatMenuOpen = true }, modifier = Modifier.size(48.dp)) {
                            UiGlyph(UiIcon.More, "Меню чата", 24.dp)
                        }
                        DropdownMenu(expanded = chatMenuOpen, onDismissRequest = { chatMenuOpen = false }) {
                            if (paired) {
                                DropdownMenuItem(text = { Text("Новый чат") }, leadingIcon = { UiGlyph(UiIcon.Plus) },
                                    onClick = { chatMenuOpen = false; onNewChat(null) })
                                DropdownMenuItem(text = { Text("Инструменты чата") },
                                    leadingIcon = { UiGlyph(UiIcon.Files) }, onClick = { chatMenuOpen = false; controlsTurn = ""; controlsOpen = true })
                                DropdownMenuItem(text = { Text("Изменения файлов") },
                                leadingIcon = { UiGlyph(UiIcon.Files) }, onClick = { chatMenuOpen = false; changesOpen = true })
                            DropdownMenuItem(text = { Text(if (taskNotifications) "Выключить уведомления" else "Включить уведомления") },
                                leadingIcon = { UiGlyph(UiIcon.Message) }, onClick = { chatMenuOpen = false; onTaskNotifications() })
                            DropdownMenuItem(text = { Text("Субагенты") }, leadingIcon = { UiGlyph(UiIcon.Agents) },
                                    onClick = { chatMenuOpen = false; subagentsOpen = true })
                            }
                            DropdownMenuItem(text = { Text(if (updateAvailable) "Есть обновление" else "Обновления") },
                                leadingIcon = { UiGlyph(UiIcon.Refresh) }, onClick = { chatMenuOpen = false; onUpdates() })
                        }
                    }
                }
            },
            bottomBar = {
                if (paired) Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background).imePadding().navigationBarsPadding()
                    .padding(horizontal = 16.dp, vertical = 8.dp)) {
                    TaskControls(selectedThreadId, activeTurnId, onSubagentRequest,
                        onChanges = { changesOpen = true }, onInterrupted = onRetryHistory)
                    Surface(shape = UiSpace.composer,
                        color = MaterialTheme.colorScheme.surfaceVariant) {
                        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                            Column(Modifier.heightIn(max = composerContentHeight)
                                .verticalScroll(rememberScrollState())) {
                            BasicTextField(input, onInput,
                                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "Сообщение для Codex" }
                                    .padding(horizontal = 10.dp, vertical = if (imeHeight > 0) 6.dp else 10.dp),
                                textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
                                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                                minLines = 1, maxLines = if (imeHeight > 0 || attachments.isNotEmpty()) 3 else 5,
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
                            }
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(1.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                TextButton(onClick = onAttach, modifier = Modifier.size(48.dp)
                                    .semantics { contentDescription = "Прикрепить файл" }) {
                                    UiGlyph(UiIcon.Plus, size = 23.dp)
                                }
                                TextButton(onClick = { filesOpen = true; onBrowseWorkspace(""); onFetchFiles() },
                                    modifier = Modifier.size(48.dp).semantics { contentDescription = "Файлы проекта" }) {
                                    UiGlyph(UiIcon.Folder, size = 22.dp)
                                }
                                TextButton(onClick = {
                                    draftModel = if (modelOverridden) selectedModel else ""
                                    draftEffort = if (effortOverridden) selectedEffort else ""
                                    modelSheet = true
                                }, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                                    contentPadding = PaddingValues(horizontal = 2.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text("$modelName · ${effortLabel(selectedEffort.ifBlank { "по умолчанию" })}",
                                            modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            maxLines = 2, overflow = TextOverflow.Ellipsis)
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
                Column(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner)
                    .verticalScroll(rememberScrollState()).padding(UiSpace.screen),
                    verticalArrangement = Arrangement.Center) {
                    Text(if (relayOnly) "Подключите компьютер" else "RemoteVibecode Classic", style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(12.dp))
                    Text(if (relayOnly)
                        "Откройте агент RemoteVibecode на ПК и отсканируйте его QR-код. Телефон подключится через ваш ретранслятор; адрес сервера и отпечаток сертификата уже включены в код."
                        else "Один раз подключите телефон к вашему ПК. Затем чаты и файлы будут доступны сразу.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(24.dp))
                    Button(onClick = onScan, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) { Text("Сканировать QR-код") }
                    Spacer(Modifier.height(14.dp))
                    Text(status, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall)
                }
            } else {
                Box(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner)) {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize().nestedScroll(historyScrollConnection),
                    contentPadding = PaddingValues(start = UiSpace.screen, end = UiSpace.screen,
                        top = 12.dp, bottom = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
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
                    if (visibleLines.isEmpty() && visibleQueue.isEmpty()) item(key = "initial-history") {
                        Box(Modifier.fillMaxWidth().padding(top = 80.dp), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                when {
                                    initialHistoryLoading -> {
                                        CircularProgressIndicator(Modifier.size(28.dp))
                                        Text("Загружаю историю…", Modifier.padding(top = 12.dp),
                                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    initialHistoryError.isNotBlank() -> {
                                        Text(initialHistoryError, color = MaterialTheme.colorScheme.error)
                                        TextButton(onClick = onRetryHistory) { Text("Повторить") }
                                    }
                                    else -> Text("Напишите сообщение для Codex", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                    itemsIndexed(visibleLines, key = { index, line -> line.id.ifBlank { "${line.turnId}:$index" } }) { index, line ->
                        when (line.role) {
                            "user" -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
                                Surface(shape = RoundedCornerShape(20.dp, 20.dp, 6.dp, 20.dp),
                                    color = MaterialTheme.colorScheme.surfaceVariant,
                                    modifier = Modifier.widthIn(max = LocalConfiguration.current.screenWidthDp.dp * 0.88f - UiSpace.screen)) {
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
                                                loadImage, Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 300.dp)
                                                    .padding(top = 9.dp).clickable { openedImage = image }, adaptivePreview = true)
                                        }
                                    }
                                }
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    if (line.time.isNotBlank()) Text(timestamp(line.time),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        style = MaterialTheme.typography.labelSmall)
                                    CopyAction(line.text)
                                    IconButton(onClick = { controlsTurn = line.turnId; controlsOpen = true }, modifier = Modifier.size(48.dp)) {
                                        UiGlyph(UiIcon.More, "Действия с этим сообщением", 20.dp)
                                    }
                                }
                            }
                            "process" -> {
                                val expanded = expandedProcesses[line.turnId] ?: false
                                Surface(shape = RoundedCornerShape(12.dp),
                                    color = MaterialTheme.colorScheme.surfaceVariant,
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { contentDescription = "Ход работы, " + if (expanded) "раскрыт" else "свёрнут" }.clickable { expandedProcesses[line.turnId] = !expanded }) {
                                    Column(Modifier.padding(horizontal = 13.dp, vertical = 10.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Text("Ход работы" + if (line.steps > 0) " · ${actionCount(line.steps)}" else "",
                                                modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                                            Spacer(Modifier.width(8.dp))
                                            UiGlyph(if (expanded) UiIcon.ChevronUp else UiIcon.ChevronDown,
                                                size = 20.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
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
                                        (if (expanded) line.activities else emptyList()).forEach { activity ->
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
                            "system" -> Row(Modifier.fillMaxWidth().padding(vertical = 3.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                UiGlyph(UiIcon.Terminal, size = 15.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.width(7.dp))
                                Text(line.text, style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            "outcome" -> Column(Modifier.fillMaxWidth().padding(vertical = 4.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                                val expanded = expandedProcesses[line.id] == true
                                val failed = line.text.substringBefore("\n").contains("ошибкой")
                                FlowRow(Modifier.fillMaxWidth().clickable { expandedProcesses[line.id] = !expanded }
                                    .heightIn(min = 48.dp).padding(vertical = 12.dp),
                                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(line.outcomeSummary.ifBlank { line.text.substringBefore("\n") },
                                        style = MaterialTheme.typography.bodySmall,
                                        color = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (line.quotaSummary.isNotBlank()) Text("· ${line.quotaSummary}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    UiGlyph(if (expanded) UiIcon.ChevronDown else UiIcon.ChevronRight,
                                        size = 16.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                if (expanded) SelectionContainer {
                                    Text(line.text, Modifier.padding(bottom = 12.dp),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            else -> Column {
                                val previous = visibleLines.subList(0, index).lastOrNull {
                                    it.role !in setOf("process", "system", "outcome")
                                }
                                val showHeader = previous == null || line.turnId.isBlank() ||
                                    previous.turnId != line.turnId || previous.role == "user"
                                if (showHeader) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("Codex", style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (line.time.isNotBlank()) {
                                        Spacer(Modifier.width(8.dp))
                                        Text(timestamp(line.time), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            style = MaterialTheme.typography.labelSmall)
                                    }
                                }
                                Spacer(Modifier.height(8.dp))
                                }
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
                                        loadImage, Modifier.fillMaxWidth().heightIn(min = 120.dp, max = 300.dp)
                                            .padding(top = 8.dp).clickable { openedImage = image }, adaptivePreview = true)
                                }
                                CopyAction(line.text)
                            }
                        }
                    }
                    items(visibleQueue, key = { "queue:${it.id}" }) { item ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                            Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.secondaryContainer,
                                modifier = Modifier.fillMaxWidth(0.86f)) {
                                Column(Modifier.padding(13.dp)) {
                                    Text(if (item.deliveredTurnId.isNotBlank())
                                        (if (item.steered) "Корректировка отправлена" else "Отправлено")
                                        else if (item.queueState == "checking") "Проверяем состояние" else "В очереди", style = MaterialTheme.typography.labelSmall,
                                        fontWeight = FontWeight.Bold,
                                        color = MaterialTheme.colorScheme.onSecondaryContainer)
                                    Spacer(Modifier.height(5.dp))
                                    Text(item.text.ifBlank { "Вложение · ${item.files.size}" },
                                        style = MaterialTheme.typography.bodyLarge)
                                    Spacer(Modifier.height(4.dp))
                                    Text(if (item.deliveredTurnId.isNotBlank()) "Ожидаю отображения в истории"
                                        else if (item.cancelRequested) "Отменяю…" else if (item.queueState == "checking") "Запись исчезла из очереди Desktop. Проверяем доставку или отмену" else if (item.acceptedByBridge)
                                        "В очереди Codex" else "Ожидает сети",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (item.nativeSubmissionId.isNotBlank() && item.deliveredTurnId.isBlank()) Text("Корректировка этой очереди недоступна в Desktop", style = MaterialTheme.typography.labelSmall)
                                    Row {
                                        if (item.acceptedByBridge && !item.cancelRequested && item.deliveredTurnId.isBlank() && item.nativeSubmissionId.isBlank() && item.queueState != "checking")
                                            TextButton(onClick = { onSteerQueued(item) }) {
                                                Text("Корректировать сейчас")
                                            }
                                        if (item.deliveredTurnId.isBlank()) TextButton(onClick = { if (item.queueState == "checking") dismissUnknown = item else onCancelQueued(item) }, enabled = !item.cancelRequested) {
                                            Text(if (item.queueState == "checking") "Скрыть уведомление" else "Отменить")
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
                                    if (last >= 0) {
                                        followBottom = true
                                        scrollToEnd()
                                    }
                                }
                            },
                        shape = CircleShape,
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        shadowElevation = 0.dp,
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
        ImageViewer("/api/chat/image?id=${Uri.encode(image.id)}", image.name, loadImage,
            onClose = { openedImage = null }, onSave = { onSaveChatImage(image) }, saveStatus = status)
    }
    if (modelSheet) ModalBottomSheet(onDismissRequest = { modelSheet = false },
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().verticalScroll(rememberScrollState())
            .padding(start = 18.dp, end = 18.dp, bottom = 36.dp)) {
            Text("Модель и усилие", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(14.dp))
            Text("Модель", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { selected = draftModel.isBlank(); role = Role.RadioButton }
                .clickable { draftModel = ""; draftEffort = "" }
                .padding(horizontal = 8.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Как в чате", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                if (draftModel.isBlank()) UiGlyph(UiIcon.Check, size = 17.dp)
            }
            if (models.isEmpty()) Text(if (catalogLoading) "Загружаю модели…" else "Модели недоступны",
                Modifier.padding(vertical = 12.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (catalogError.isNotBlank()) {
                Text(catalogError, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                TextButton(onClick = onRefreshCatalog, enabled = !catalogLoading) { Text("Повторить") }
            }
                models.forEach { model ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { selected = model.id == draftModel; role = Role.RadioButton }
                        .clickable { draftModel = model.id; draftEffort = "" }
                        .padding(horizontal = 8.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(model.name, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                            fontWeight = if (model.id == draftModel) FontWeight.SemiBold else FontWeight.Normal)
                        if (model.id == draftModel) UiGlyph(UiIcon.Check, size = 17.dp)
                    }
                }
            Spacer(Modifier.height(12.dp))
            Text("Усилие", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            val effortOptions = models.firstOrNull { it.id == draftModel.ifBlank { selectedModel } }?.efforts ?: emptyList()
            androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(7.dp),
                verticalArrangement = Arrangement.spacedBy(7.dp)) {
                Surface(shape = RoundedCornerShape(18.dp),
                    color = if (draftEffort.isBlank()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.heightIn(min = 48.dp).semantics { selected = draftEffort.isBlank(); role = Role.RadioButton }
                        .clickable { draftEffort = "" }) {
                    Text("Как в чате", modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (draftEffort.isBlank()) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)
                }
                effortOptions.forEach { effort ->
                    Surface(shape = RoundedCornerShape(18.dp),
                        color = if (effort == draftEffort) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant,
                        modifier = Modifier.heightIn(min = 48.dp).semantics { selected = draftEffort == effort; role = Role.RadioButton }
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
