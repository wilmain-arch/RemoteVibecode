package ru.wilmain.codexphone

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.material3.IconButton
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import android.net.Uri

@Composable
internal fun WorkspaceFilesScreen(
    projectName: String, rootName: String, threadId: String, path: String, entries: List<WorkspaceEntry>,
    loading: Boolean, error: String, truncated: Boolean,
    previewPath: String, previewText: String, previewNote: String, previewLoading: Boolean,
    fileStatus: String,
    outboxFiles: List<RemoteFile>, outboxLoading: Boolean, outboxError: String,
    transferringFileId: String, transferProgress: Pair<Long, Long>?,
    pendingFiles: List<PendingFile>,
    loadImage: suspend (String) -> ByteArray,
    onCancelTransfer: () -> Unit,
    onClose: () -> Unit, onBrowse: (String) -> Unit, onPreview: (String) -> Unit,
    onAsk: (String) -> Unit, onSaveWorkspace: (WorkspaceEntry) -> Unit,
    onSaveOutbox: (RemoteFile) -> Unit, onFetchOutbox: () -> Unit, onAttach: () -> Unit,
    onProjectFile: (String) -> Unit, saveStatus: String = "",
) {
    var transferTab by rememberSaveable { mutableStateOf(false) }
    var search by rememberSaveable(path) { mutableStateOf("") }
    var imageOpen by rememberSaveable(previewPath) { mutableStateOf(false) }
    val linkedFile = path.startsWith("@chat-files/")
    val selectedFile = entries.firstOrNull { it.path == previewPath }
    fun back() {
        when {
            previewPath.isNotBlank() -> onPreview("")
            linkedFile && !transferTab -> onBrowse("")
            path.isNotBlank() && !transferTab -> onBrowse(path.substringBeforeLast('/', ""))
            else -> onClose()
        }
    }
    BackHandler { back() }
    val title = when {
        previewPath.isNotBlank() -> previewPath.substringAfterLast('/')
        transferTab -> "Передача файлов"
        linkedFile -> "Файл из чата"
        path.isNotBlank() -> path.substringAfterLast('/')
        else -> "Файлы проекта"
    }
    val subtitle = when {
        linkedFile -> "Вне проекта · просмотр и скачивание"
        previewPath.isNotBlank() -> "${projectName.ifBlank { rootName }} / ${previewPath.substringBeforeLast('/', rootName)}" +
            (selectedFile?.let { " · ${fileSize(it.size)}" } ?: "")
        transferTab -> if (projectName.isBlank() || projectName == rootName)
            rootName.ifBlank { "Проект" } else "$projectName · ${rootName.ifBlank { "Проект" }}"
        path.isNotBlank() -> "${projectName.ifBlank { rootName }} · $path"
        else -> if (projectName.isBlank() || projectName == rootName) rootName.ifBlank { "Проект" }
            else "$projectName · ${rootName.ifBlank { "Проект" }}"
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            UiScreenHeader(title, ::back, subtitle = subtitle, actions = {
                if (previewPath.isBlank()) IconButton(onClick = {
                    if (transferTab) onFetchOutbox() else onBrowse(path)
                }, modifier = Modifier.semantics { contentDescription = "Обновить" }) {
                    UiGlyph(UiIcon.Refresh, size = 22.dp)
                }
            })
        },
        bottomBar = {
            if (previewPath.isNotBlank()) {
                Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)
                    .imePadding().navigationBarsPadding().padding(start = 17.dp, end = 17.dp, top = 8.dp, bottom = 9.dp)) {
                    if (fileStatus.isNotBlank()) FileStatus(fileStatus)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                        Button(onClick = { onAsk(previewPath) }, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                            shape = RoundedCornerShape(11.dp),
                            contentPadding = PaddingValues(horizontal = 5.dp)) {
                            Text("Спросить Codex", maxLines = 1,
                                style = MaterialTheme.typography.labelMedium)
                        }
                        Button(onClick = { selectedFile?.let(onSaveWorkspace) },
                            enabled = selectedFile != null && selectedFile.size <= 40L * 1024 * 1024 &&
                                transferringFileId.isBlank(),
                            modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                            shape = RoundedCornerShape(11.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.surfaceVariant,
                                contentColor = MaterialTheme.colorScheme.onSurface),
                            contentPadding = PaddingValues(horizontal = 5.dp)) {
                            Text("Сохранить на телефон", maxLines = 2,
                                style = MaterialTheme.typography.labelSmall)
                        }
                    }
                    if (transferringFileId == previewPath && transferProgress != null)
                        FileTransferProgress(transferProgress)
                    if (transferringFileId == previewPath)
                        TextButton(onClick = onCancelTransfer) { Text("Отменить передачу") }
                }
            } else {
                Row(Modifier.fillMaxWidth().imePadding().navigationBarsPadding()
                    .padding(start = 14.dp, end = 14.dp, top = 5.dp, bottom = 10.dp)
                    .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(18.dp))
                    .padding(5.dp), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    FileDockItem("Проект", !transferTab, true, Modifier.weight(1f)) { transferTab = false }
                    FileDockItem("Передача", transferTab, false, Modifier.weight(1f)) {
                        transferTab = true
                        onFetchOutbox()
                    }
                }
            }
        },
    ) { inner ->
        if (previewPath.isNotBlank()) {
            LazyColumn(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner),
                contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 24.dp)) {
                item {
                    if (selectedFile?.let { isSupportedImage(it.name) } == true)
                        RemoteImage("/api/workspace/image?threadId=${Uri.encode(threadId)}&path=${Uri.encode(previewPath)}",
                            selectedFile.name, loadImage, Modifier.fillMaxWidth().heightIn(min = 180.dp, max = 360.dp)
                                .clickable(onClickLabel = "Открыть изображение") { imageOpen = true }, adaptivePreview = true)
                    else if (previewLoading) CircularProgressIndicator(Modifier.padding(20.dp))
                    else if (previewNote.isNotBlank() && previewText.isBlank())
                        Text(previewNote, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    else {
                        if (previewNote.isNotBlank()) Text(previewNote,
                            Modifier.padding(bottom = 10.dp),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall)
                        SelectionContainer {
                            if (selectedFile?.name?.endsWith(".md", ignoreCase = true) == true)
                                MarkdownContent(previewText.take(20000), compact = true, onProjectFile = { reference ->
                                    val resolved = if (reference.startsWith("/") || reference.startsWith("@chat-files/") || reference.contains("://")) reference
                                        else java.nio.file.Paths.get(previewPath.substringBeforeLast('/', ""))
                                            .resolve(reference).normalize().toString()
                                    onProjectFile(resolved)
                                })
                            else Text(previewText.take(20000),
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall)
                        }
                        if (previewText.length > 20000) Text("Показаны первые 20 000 символов",
                            Modifier.padding(top = 12.dp),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        } else if (transferTab) {
            LazyColumn(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner),
                contentPadding = PaddingValues(start = 18.dp, end = 18.dp, top = 14.dp, bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(18.dp)) {
                item {
                    Surface(color = MaterialTheme.colorScheme.surfaceVariant,
                        contentColor = MaterialTheme.colorScheme.onSurface,
                        shape = RoundedCornerShape(17.dp)) {
                        Column(Modifier.fillMaxWidth().padding(16.dp)) {
                            SectionLabel("С ТЕЛЕФОНА В ЧАТ")
                            Text("Добавить файл к сообщению", Modifier.padding(top = 8.dp),
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold)
                            Text("Выбранный файл появится рядом с полем ввода и отправится вместе со следующим сообщением.",
                                Modifier.padding(top = 5.dp, bottom = 12.dp),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Button(onClick = onAttach, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                                shape = RoundedCornerShape(11.dp)) { Text("Выбрать файл на телефоне") }
                            if (pendingFiles.isNotEmpty()) Text("Готово к отправке: ${pendingFiles.joinToString { it.name }}",
                                Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                item {
                    SectionLabel("С ПК НА ТЕЛЕФОН · OUTBOX")
                    if (fileStatus.isNotBlank()) FileStatus(fileStatus)
                    if (transferringFileId.isNotBlank())
                        TextButton(onClick = onCancelTransfer) { Text("Отменить передачу") }
                    if (outboxLoading) CircularProgressIndicator(Modifier.padding(top = 14.dp))
                    if (outboxError.isNotBlank()) {
                        Text(outboxError, color = MaterialTheme.colorScheme.error)
                        TextButton(onClick = onFetchOutbox) { Text("Повторить") }
                    }
                    if (!outboxLoading && outboxError.isBlank() && outboxFiles.isEmpty())
                        Text("Пока нет файлов для скачивания",
                            Modifier.padding(top = 14.dp),
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (outboxFiles.isNotEmpty()) item {
                    Surface(color = MaterialTheme.colorScheme.surfaceVariant,
                        contentColor = MaterialTheme.colorScheme.onSurface,
                        shape = RoundedCornerShape(17.dp)) {
                        Column(Modifier.padding(horizontal = 14.dp, vertical = 6.dp)) {
                            outboxFiles.forEach { file ->
                                Row(Modifier.fillMaxWidth().heightIn(min = 64.dp),
                                    verticalAlignment = Alignment.CenterVertically) {
                                    if (isSupportedImage(file.name))
                                        RemoteImage("/api/outbox/image?id=${Uri.encode(file.id)}",
                                            file.name, loadImage, Modifier.size(37.dp), thumbnail = true)
                                    else Box(Modifier.width(37.dp)) {
                                        UiGlyph(UiIcon.File, size = 21.dp,
                                            tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    Column(Modifier.weight(1f)) {
                                        Text(file.name, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = FontWeight.SemiBold)
                                        Text(fileSize(file.size), style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    Button(onClick = { onSaveOutbox(file) },
                                        enabled = transferringFileId.isBlank(),
                                        shape = RoundedCornerShape(10.dp),
                                        colors = ButtonDefaults.buttonColors(
                                            containerColor = MaterialTheme.colorScheme.background,
                                            contentColor = MaterialTheme.colorScheme.onSurface),
                                        contentPadding = PaddingValues(horizontal = 10.dp),
                                        modifier = Modifier.heightIn(min = 48.dp)) { Text("Сохранить") }
                                }
                                if (transferringFileId == file.id && transferProgress != null)
                                    FileTransferProgress(transferProgress)
                            }
                        }
                    }
                }
                item { Text("Сохранение откроет системный выбор папки Android.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        } else {
            Column(Modifier.fillMaxSize().padding(inner).consumeWindowInsets(inner)) {
                Surface(Modifier.fillMaxWidth().padding(horizontal = UiSpace.screen, vertical = 10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(12.dp)) {
                    BasicTextField(search, { search = it }, singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 13.dp),
                        textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.secondary),
                        decorationBox = { innerField ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                UiGlyph(UiIcon.Search, size = 18.dp,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.width(8.dp))
                                Box(Modifier.weight(1f)) {
                                    if (search.isEmpty()) Text("Найти в этой папке",
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        style = MaterialTheme.typography.bodyMedium)
                                    innerField()
                                }
                                if (search.isNotBlank()) IconButton(onClick = { search = "" },
                                    modifier = Modifier.semantics { contentDescription = "Очистить поиск" }) {
                                    UiGlyph(UiIcon.Close, size = 18.dp)
                                }
                            }
                        })
                }
                if (loading) CircularProgressIndicator(Modifier.padding(22.dp))
                if (error.isNotBlank()) {
                    Text(error, Modifier.padding(horizontal = UiSpace.screen),
                        color = MaterialTheme.colorScheme.error)
                    TextButton(onClick = { onBrowse(path) }) { Text("Повторить") }
                }
                val query = search.trim().filter { it.isLetterOrDigit() }.lowercase()
                val filtered = entries.filter { entry ->
                    entry.name.contains(search.trim(), ignoreCase = true) ||
                        (query.isNotEmpty() && entry.name.filter { it.isLetterOrDigit() }.lowercase().contains(query))
                }
                val folders = filtered.filter { it.isDirectory }
                val files = filtered.filterNot { it.isDirectory }
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 20.dp)) {
                    if (!loading && error.isBlank() && filtered.isEmpty()) item {
                        Text(if (search.isBlank()) "Папка пуста" else "Ничего не найдено в этой папке",
                            Modifier.padding(20.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (folders.isNotEmpty()) item { SectionLabel("Папки", Modifier.padding(start = 21.dp, top = 18.dp, bottom = 9.dp)) }
                    items(folders, key = { it.path }) { entry ->
                        WorkspaceRow(entry, threadId, loadImage, onClick = { onBrowse(entry.path) })
                    }
                    if (files.isNotEmpty()) item { SectionLabel("Файлы", Modifier.padding(start = 21.dp, top = 23.dp, bottom = 9.dp)) }
                    items(files, key = { it.path }) { entry ->
                        WorkspaceRow(entry, threadId, loadImage, onClick = { onPreview(entry.path) })
                    }
                    if (truncated) item { Text("Показаны первые 200 элементов папки",
                        Modifier.padding(20.dp), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }
        }
    }
    if (imageOpen && selectedFile != null) ImageViewer(
        "/api/workspace/image?threadId=${Uri.encode(threadId)}&path=${Uri.encode(previewPath)}",
        selectedFile.name, loadImage, onClose = { imageOpen = false },
        onSave = { onSaveWorkspace(selectedFile) }, saveStatus = saveStatus)

}

@Composable
private fun FileDockItem(label: String, active: Boolean, project: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Surface(modifier.heightIn(min = 48.dp).semantics { selected = active; contentDescription = label }
        .clickable(onClick = onClick),
        color = if (active) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(13.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
        ) {
            Spacer(Modifier.weight(1f))
            UiGlyph(if (project) UiIcon.Folder else UiIcon.Upload, size = 19.dp,
                tint = if (active) MaterialTheme.colorScheme.onSecondaryContainer
                    else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(label, style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.SemiBold,
                color = if (active) MaterialTheme.colorScheme.onSecondaryContainer
                    else MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
        }
    }
}

@Composable
private fun SectionLabel(label: String, modifier: Modifier = Modifier) {
    Text(label, modifier, style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun WorkspaceRow(entry: WorkspaceEntry, threadId: String,
                         loadImage: suspend (String) -> ByteArray, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 72.dp)
        .semantics { contentDescription = if (entry.isDirectory) "Папка ${entry.name}" else "Файл ${entry.name}" }
        .clickable(onClick = onClick).padding(horizontal = UiSpace.screen),
        verticalAlignment = Alignment.CenterVertically) {
        if (entry.isDirectory) Box(Modifier.width(60.dp)) {
            UiGlyph(UiIcon.Folder, size = 21.dp, tint = MaterialTheme.colorScheme.secondary)
        } else if (isSupportedImage(entry.name)) Box(Modifier.width(60.dp)) {
            RemoteImage("/api/workspace/image?threadId=${Uri.encode(threadId)}&path=${Uri.encode(entry.path)}",
                entry.name, loadImage, Modifier.size(48.dp).clip(RoundedCornerShape(8.dp)), thumbnail = true)
        } else Box(Modifier.width(60.dp)) {
            UiGlyph(UiIcon.File, size = 21.dp,
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Column(Modifier.weight(1f)) {
            Text(entry.name, maxLines = 2, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold)
            if (!entry.isDirectory) Text(fileSize(entry.size),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        UiGlyph(UiIcon.ChevronRight, size = 18.dp,
            tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun FileStatus(value: String) {
    Text(value, Modifier.padding(bottom = 8.dp),
        style = MaterialTheme.typography.bodySmall,
        color = if (value.startsWith("Не удалось") || value.startsWith("Ошибка"))
            MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun FileTransferProgress(progress: Pair<Long, Long>) {
    val (done, total) = progress
    LinearProgressIndicator(progress = { if (total > 0) (done.toFloat() / total).coerceIn(0f, 1f) else 0f },
        modifier = Modifier.fillMaxWidth().padding(top = 6.dp))
    Text("${fileSize(done)} из ${fileSize(total)}", style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}
