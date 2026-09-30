package ru.wilmain.codexphone

import android.text.format.DateUtils
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@Composable
internal fun ProjectsScreen(
    projects: List<ProjectGroup>, selectedThreadId: String,
    themeMode: String, onThemeMode: (String) -> Unit,
    usageLimits: UsageLimits?, limitsLoading: Boolean, limitsError: String,
    onClose: () -> Unit, onSelectThread: (String) -> Unit,
    onDeleteThread: (String) -> Unit,
    onMoveThread: (String, String) -> Unit,
    actionError: String,
    onNewChat: (String?) -> Unit, onRefreshCatalog: () -> Unit,
    onRefreshLimits: () -> Unit,
    onDevices: () -> Unit,
    onDisconnect: () -> Unit,
) {
    val context = LocalContext.current
    fun chatCount(value: Int): String = when {
        value % 10 == 1 && value % 100 != 11 -> "$value чат"
        value % 10 in 2..4 && value % 100 !in 12..14 -> "$value чата"
        else -> "$value чатов"
    }
    BackHandler(onBack = onClose)
    var search by remember { mutableStateOf("") }
    var deleteCandidate by remember { mutableStateOf<ThreadItem?>(null) }
    var moveCandidate by remember { mutableStateOf<ThreadItem?>(null) }
    val expanded = remember { mutableStateMapOf<String, Boolean>() }
    deleteCandidate?.let { thread ->
        AlertDialog(
            onDismissRequest = { deleteCandidate = null },
            title = { Text("Удалить чат?") },
            text = { Text("«${thread.title}» будет удалён из Codex Desktop. Это действие нельзя отменить.") },
            confirmButton = { TextButton(onClick = {
                deleteCandidate = null
                onDeleteThread(thread.id)
            }) { Text("Удалить", color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = { deleteCandidate = null }) { Text("Отмена") } },
        )
    }
    moveCandidate?.let { thread ->
        AlertDialog(
            onDismissRequest = { moveCandidate = null },
            title = { Text("Переместить чат") },
            text = {
                Column(Modifier.heightIn(max = 400.dp).verticalScroll(rememberScrollState())) {
                    (projects.filter { it.id != "other" }.map { it.id to it.name } +
                        ("" to "Без проекта")).forEach { (id, name) ->
                        TextButton(onClick = {
                            moveCandidate = null
                            onMoveThread(thread.id, id)
                        }, modifier = Modifier.fillMaxWidth()) {
                            Text(name, modifier = Modifier.fillMaxWidth())
                        }
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { moveCandidate = null }) { Text("Отмена") } },
        )
    }
    fun threadTime(value: Long): String = if (value <= 0) "" else
        DateUtils.getRelativeTimeSpanString(value, System.currentTimeMillis(), DateUtils.DAY_IN_MILLIS,
            DateUtils.FORMAT_ABBREV_RELATIVE).toString()
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            Row(Modifier.fillMaxWidth().statusBarsPadding().height(70.dp).padding(horizontal = 12.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onClose, modifier = Modifier.size(48.dp)
                    .semantics { contentDescription = "Назад к чату" }) { UiGlyph(UiIcon.Back, size = 22.dp) }
                Column(Modifier.weight(1f).padding(start = 4.dp)) {
                    Text("Проекты", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text("Проектов: ${projects.count { it.id != "other" }} · Чатов: ${projects.sumOf { it.threads.size }}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                TextButton(onClick = { onNewChat(null) }, modifier = Modifier.size(48.dp)
                    .semantics { contentDescription = "Новый чат" }) { UiGlyph(UiIcon.Plus, size = 23.dp) }
            }
        },
    ) { inner ->
        Column(Modifier.fillMaxSize().padding(inner)) {
            Surface(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp),
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
                            Box {
                                if (search.isEmpty()) Text("Поиск проектов и чатов",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    style = MaterialTheme.typography.bodyMedium)
                                innerField()
                            }
                        }
                    })
            }
            if (actionError.isNotBlank()) Text(actionError,
                Modifier.padding(horizontal = 22.dp, vertical = 4.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
            Surface(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 4.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(18.dp)) {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 13.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("Лимиты Codex", style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold)
                        TextButton(onClick = onRefreshLimits, enabled = !limitsLoading) {
                            Text(if (limitsLoading) "Обновляю…" else "Обновить")
                        }
                    }
                    if (usageLimits != null) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                            LimitColumn("5 часов", usageLimits.fiveHours, Modifier.weight(1f))
                            LimitColumn("Неделя", usageLimits.week, Modifier.weight(1f))
                        }
                        if (usageLimits.updatedAt > 0) Text(
                            "Обновлено " + DateUtils.formatDateTime(context, usageLimits.updatedAt * 1000,
                                DateUtils.FORMAT_SHOW_TIME),
                            Modifier.padding(top = 10.dp), style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else Text(if (limitsLoading) "Получаю данные аккаунта…"
                        else limitsError.ifBlank { "Данные о лимитах недоступны" },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (usageLimits != null && limitsError.isNotBlank()) Text(limitsError,
                        Modifier.padding(top = 5.dp), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 20.dp)) {
                projects.forEach { group ->
                    val isStandalone = group.id == "other"
                    val matches = group.threads.filter { search.isBlank() ||
                        group.name.contains(search, ignoreCase = true) ||
                        it.title.contains(search, ignoreCase = true) }
                    if (matches.isNotEmpty() || (!isStandalone &&
                            (search.isBlank() || group.name.contains(search, ignoreCase = true)))) {
                        val isCurrent = group.threads.any { it.id == selectedThreadId }
                        val isExpanded = isStandalone || search.isNotBlank() || (expanded[group.id] ?: false)
                        item(key = "heading:${group.id}") {
                            if (isStandalone) {
                                Text("ЧАТЫ", Modifier.padding(start = 21.dp, top = 23.dp, bottom = 9.dp),
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            } else {
                                Row(Modifier.fillMaxWidth().clickable { expanded[group.id] = !isExpanded }
                                    .padding(horizontal = 22.dp, vertical = 11.dp),
                                    verticalAlignment = Alignment.CenterVertically) {
                                    UiGlyph(UiIcon.Folder, size = 18.dp,
                                        tint = MaterialTheme.colorScheme.secondary)
                                    Spacer(Modifier.width(14.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(group.name, style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = FontWeight.SemiBold)
                                        Text(chatCount(matches.size), style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    UiGlyph(if (isExpanded) UiIcon.ChevronDown else UiIcon.ChevronRight,
                                        size = 18.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (isCurrent) TextButton(onClick = { onNewChat(group.cwd) },
                                        modifier = Modifier.size(48.dp).semantics {
                                            contentDescription = "Новый чат в ${group.name}"
                                        }) { UiGlyph(UiIcon.Plus, size = 19.dp) }
                                }
                            }
                        }
                        if (isExpanded) items(matches.size, key = { index -> "thread:${matches[index].id}" }) { index ->
                            val thread = matches[index]
                            val isSelected = thread.id == selectedThreadId
                            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 2.dp)
                                .background(if (isSelected) MaterialTheme.colorScheme.surfaceVariant
                                    else MaterialTheme.colorScheme.background, RoundedCornerShape(12.dp))
                                .semantics {
                                    selected = isSelected
                                    contentDescription = "Чат: ${thread.title}"
                                }
                                .clickable { onSelectThread(thread.id) }
                                .padding(start = 20.dp, end = 14.dp, top = 12.dp, bottom = 12.dp),
                                verticalAlignment = Alignment.CenterVertically) {
                                UiGlyph(UiIcon.Message, size = 17.dp,
                                    tint = if (thread.status == "active") MaterialTheme.colorScheme.secondary
                                        else MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(thread.title, maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal)
                                    if (thread.updatedAt > 0) Text(threadTime(thread.updatedAt),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                TextButton(onClick = { moveCandidate = thread },
                                    modifier = Modifier.size(48.dp).semantics {
                                        contentDescription = "Переместить чат: ${thread.title}"
                                    }) { UiGlyph(UiIcon.Folder, size = 18.dp,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                                TextButton(onClick = { deleteCandidate = thread },
                                    modifier = Modifier.size(48.dp).semantics {
                                        contentDescription = "Удалить чат: ${thread.title}"
                                    }) { UiGlyph(UiIcon.Trash, size = 18.dp,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                            }
                        }
                    }
                }
                item(key = "settings") {
                Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)
                    .padding(horizontal = 18.dp, vertical = 8.dp)) {
                    Text("ТЕМА", style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(7.dp))
                    Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(16.dp)).padding(4.dp),
                        horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        listOf("system" to "Система", "light" to "Светлая", "dark" to "Тёмная").forEach { (mode, label) ->
                            val isSelected = themeMode == mode
                            Surface(
                                modifier = Modifier.weight(1f).height(44.dp).semantics {
                                    selected = isSelected
                                    contentDescription = "Тема: $label"
                                }.clickable { onThemeMode(mode) },
                                color = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
                                shape = RoundedCornerShape(12.dp),
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Text(label, style = MaterialTheme.typography.labelMedium,
                                        color = if (isSelected) MaterialTheme.colorScheme.onPrimary
                                            else MaterialTheme.colorScheme.onSurfaceVariant,
                                        fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal)
                                }
                            }
                        }
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        TextButton(onClick = onRefreshCatalog) { Text("Обновить список") }
                        TextButton(onClick = onDevices) { Text("Устройства ADB") }
                        TextButton(onClick = onDisconnect) { Text("Отключить") }
                    }
                }
                }
            }
        }
    }
}

@Composable
private fun LimitColumn(label: String, window: LimitWindow?, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    Column(modifier.semantics {
        contentDescription = if (window == null) "$label: данные недоступны"
            else "$label: осталось ${window.remainingPercent} процентов"
    }) {
        Text(label, style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(if (window == null) "—" else "${window.remainingPercent}%",
            Modifier.padding(top = 2.dp), style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold)
        Box(Modifier.fillMaxWidth().padding(top = 6.dp).height(5.dp)
            .background(MaterialTheme.colorScheme.background, RoundedCornerShape(3.dp))) {
            if (window != null) Box(Modifier.fillMaxWidth(window.remainingPercent / 100f)
                .height(5.dp).background(MaterialTheme.colorScheme.primary, RoundedCornerShape(3.dp)))
        }
        Text(if (window != null && window.resetsAt > 0) "Сброс " + DateUtils.formatDateTime(
            context, window.resetsAt * 1000,
            DateUtils.FORMAT_SHOW_DATE or DateUtils.FORMAT_SHOW_TIME or DateUtils.FORMAT_ABBREV_MONTH)
            else "Время сброса неизвестно",
            Modifier.padding(top = 7.dp), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
