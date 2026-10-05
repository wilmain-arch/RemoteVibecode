package ru.wilmain.codexphone

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.sp
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray

internal data class FileArtifact(val id: String, val path: String, val diff: String,
    val status: String, val truncated: Boolean, val kind: String = "update")

internal fun parseFileArtifacts(array: JSONArray?): List<FileArtifact> =
    if (array == null) emptyList() else (0 until array.length()).mapNotNull { index ->
        array.optJSONObject(index)?.let { item -> FileArtifact(item.optString("id"),
            item.optString("path"), item.optString("diff"), item.optString("status"), item.optBoolean("truncated"), item.optJSONObject("kind")?.optString("type", "update") ?: "update") }
    }

private fun artifactPatch(file: FileArtifact): String {
    if (file.diff.startsWith("diff --git ") || file.diff.startsWith("--- ")) return file.diff.trimEnd() + "\n"
    // Preserve the published hunk, without reading today's version of the file.
    val path = file.path.replace('\n', '_').replace('\r', '_').replace('\t', '_')
    val before = if (file.kind == "add") "/dev/null" else "a/$path"
    val after = if (file.kind == "delete") "/dev/null" else "b/$path"
    return "--- $before\n+++ $after\n" + file.diff.trimEnd() + "\n"
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun FileArtifacts(files: List<FileArtifact>, turnId: String, onFile: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var pendingPatch by rememberSaveable(turnId) { mutableStateOf("") }
    var notice by remember(turnId) { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/x-patch")) { uri ->
        if (uri != null) {
            val snapshot = pendingPatch
            scope.launch {
                saving = true
                try {
                    require(snapshot.isNotBlank()) {
                        "Полный diff недоступен — сохранение отменено"
                    }
                    withContext(Dispatchers.IO) {
                        context.contentResolver.openOutputStream(uri, "wt")?.use { output ->
                            output.write(snapshot.toByteArray(Charsets.UTF_8))
                        } ?: error("Не удалось открыть файл для записи")
                    }
                    notice = "Patch сохранён"
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    notice = error.message ?: "Не удалось сохранить patch"
                } finally { saving = false }
            }
        }
    }
    val totals = remember(files) { files.map { diffStats(it.diff) }.fold(0 to 0) { sum, value ->
        (sum.first + value.first) to (sum.second + value.second)
    } }
    val complete = files.isNotEmpty() && files.none { it.truncated || it.diff.isBlank() }
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Изменения файлов", style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(fileCount(files.size), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    DiffStats(totals.first, totals.second)
                }
            }
            IconButton(enabled = !saving && complete, onClick = {
                pendingPatch = files.joinToString("") { artifactPatch(it) }; picker.launch("task-changes.patch")
            }) { UiGlyph(UiIcon.Download, "Сохранить все изменения в .patch", 20.dp) }
        }
        files.forEach { file ->
            key(file.id) {
                var expanded by rememberSaveable(turnId, file.id) { mutableStateOf(false) }
                val stats = remember(file.diff) { diffStats(file.diff) }
                Column {
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                        .clickable(role = Role.Button, onClickLabel = if (expanded) "Свернуть изменения" else "Раскрыть изменения") { expanded = !expanded }
                        .semantics { stateDescription = if (expanded) "Раскрыто" else "Свёрнуто" }
                        .heightIn(min = 56.dp).padding(vertical = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        UiGlyph(UiIcon.File, size = 20.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(file.path.substringAfterLast('/').ifBlank { "Файл" },
                                style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                            val folder = file.path.substringBeforeLast('/', "")
                            if (folder.isNotBlank()) Text(folder, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                                DiffStats(stats.first, stats.second)
                                val stateLabel = when {
                                    file.status == "failed" -> "Не применено"
                                    file.status == "inProgress" -> "Выполняется"
                                    file.truncated -> "Сокращённый diff"
                                    file.kind == "add" -> "Новый файл"
                                    file.kind == "delete" -> "Удалён"
                                    else -> ""
                                }
                                if (stateLabel.isNotBlank()) Text(stateLabel, style = MaterialTheme.typography.labelSmall,
                                    color = if (file.status == "failed") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        UiGlyph(if (expanded) UiIcon.ChevronUp else UiIcon.ChevronDown,
                            size = 18.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (expanded) {
                        if (file.diff.isBlank()) Text("Codex не опубликовал строки изменений",
                            Modifier.padding(vertical = 12.dp), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        else ArtifactDiff(file.diff)
                        if (file.truncated) Text("Показана часть изменений. Полный patch недоступен.",
                            Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error)
                        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                            TextButton(onClick = { onFile(file.path) }) {
                                UiGlyph(UiIcon.Folder, size = 16.dp); Spacer(Modifier.width(8.dp)); Text("К файлу")
                            }
                            TextButton(enabled = file.diff.isNotBlank(), onClick = {
                                clipboard.setText(androidx.compose.ui.text.AnnotatedString(file.diff)); notice = "Diff скопирован"
                            }) {
                                UiGlyph(UiIcon.Copy, size = 16.dp); Spacer(Modifier.width(8.dp)); Text("Копировать")
                            }
                            TextButton(enabled = !saving && !file.truncated && file.diff.isNotBlank(), onClick = {
                                pendingPatch = artifactPatch(file); picker.launch(file.path.substringAfterLast('/').ifBlank { "file" } + ".patch")
                            }) {
                                UiGlyph(UiIcon.Download, size = 16.dp); Spacer(Modifier.width(8.dp)); Text(".patch")
                            }
                        }
                    }
                }
            }
        }
        if (!complete) Text("Экспорт доступен только для файлов с полным diff",
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (saving) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (notice.isNotBlank()) Text(notice, Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun fileCount(count: Int): String = when {
    count % 100 in 11..14 -> "$count файлов"
    count % 10 == 1 -> "$count файл"
    count % 10 in 2..4 -> "$count файла"
    else -> "$count файлов"
}

private fun diffStats(diff: String): Pair<Int, Int> {
    var added = 0; var removed = 0
    diff.lineSequence().forEach { line ->
        if (line.startsWith('+') && !line.startsWith("+++")) added++
        if (line.startsWith('-') && !line.startsWith("---")) removed++
    }
    return added to removed
}

@Composable
private fun DiffStats(added: Int, removed: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("+$added", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.secondary)
        Text("−$removed", style = MaterialTheme.typography.labelSmall, fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.error)
    }
}

private data class DiffRow(val text: String, val kind: Char, val old: Int? = null, val new: Int? = null)
private val hunkPattern = Regex("^@@ -(\\d+)(?:,\\d+)? [+](\\d+)(?:,\\d+)? @@.*")
private fun diffRows(diff: String): List<DiffRow> {
    var old: Int? = null; var new: Int? = null
    return diff.lineSequence().toList().let { if (it.lastOrNull() == "") it.dropLast(1) else it }.map { line ->
        val hunk = hunkPattern.matchEntire(line)
        when {
            hunk != null -> { old = hunk.groupValues[1].toIntOrNull(); new = hunk.groupValues[2].toIntOrNull(); DiffRow(line, '@') }
            line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("diff ") || line.startsWith("index ") -> DiffRow(line, '@')
            line.startsWith('+') -> DiffRow(line, '+', new = new).also { new = new?.plus(1) }
            line.startsWith('-') -> DiffRow(line, '-', old = old).also { old = old?.plus(1) }
            line.startsWith(' ') -> DiffRow(line, ' ', old, new).also { old = old?.plus(1); new = new?.plus(1) }
            else -> DiffRow(line, '@')
        }
    }
}

@Composable
private fun ArtifactDiff(diff: String) {
    val rows = remember(diff) { diffRows(diff) }
    val colors = MaterialTheme.colorScheme
    val density = LocalDensity.current
    val horizontal = rememberScrollState()
    val vertical = rememberLazyListState()
    val codeStyle = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace,
        fontSize = 12.sp, lineHeight = 20.sp, letterSpacing = 0.sp)
    val lineHeight = with(density) { 20.sp.toDp() } + 4.dp
    val viewportHeight = (lineHeight * rows.size.coerceAtLeast(1)).coerceAtMost(288.dp)
    Surface(shape = RoundedCornerShape(12.dp), color = colors.surfaceVariant) {
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val longest = remember(rows) { rows.maxOfOrNull { it.text.length }?.coerceAtMost(1024) ?: 0 }
            val codeWidth = with(density) { (12.sp.toDp() * (longest + 14) * 0.62f) }.coerceAtLeast(maxWidth)
            Box(Modifier.fillMaxWidth().horizontalScroll(horizontal)) {
                SelectionContainer {
                    LazyColumn(Modifier.width(codeWidth).height(viewportHeight), state = vertical) {
                        items(rows) { row ->
                            val background = when (row.kind) {
                                '+' -> colors.secondary.copy(alpha = 0.09f)
                                '-' -> colors.error.copy(alpha = 0.09f)
                                else -> Color.Transparent
                            }
                            val foreground = when (row.kind) {
                                '+' -> colors.secondary
                                '-' -> colors.error
                                '@' -> colors.onSurfaceVariant
                                else -> colors.onSurface
                            }
                            Row(Modifier.fillMaxWidth().background(background).padding(horizontal = 8.dp, vertical = 2.dp),
                                verticalAlignment = Alignment.Top) {
                                Text((row.old?.toString() ?: "").padStart(4) + " " + (row.new?.toString() ?: "").padStart(4),
                                    style = codeStyle, color = colors.onSurfaceVariant, modifier = Modifier.padding(end = 12.dp)
                                        .clearAndSetSemantics {})
                                Text(row.text, style = codeStyle, color = foreground, softWrap = false,
                                    modifier = Modifier.weight(1f).semantics {
                                        contentDescription = when (row.kind) {
                                            '+' -> "Добавлено, строка ${row.new ?: ""}: ${row.text.drop(1)}"
                                            '-' -> "Удалено, строка ${row.old ?: ""}: ${row.text.drop(1)}"
                                            else -> row.text
                                        }
                                    })
                            }
                        }
                    }
                }
            }
        }
    }
    if (rows.any { it.text.length > 1024 }) Text("Очень длинные строки показаны частично. Кнопка «Копировать» сохраняет полный diff.",
        Modifier.padding(top = 6.dp), style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
    if (viewportHeight >= 288.dp || horizontal.maxValue > 0) Text("Прокрутите код, чтобы увидеть остальные строки",
        Modifier.padding(top = 6.dp), style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
}
