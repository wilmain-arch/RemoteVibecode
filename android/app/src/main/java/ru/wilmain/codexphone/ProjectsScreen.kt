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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.IconButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ProjectsScreen(
    projects: List<ProjectGroup>, selectedThreadId: String,
    catalogLoading: Boolean, catalogError: String,
    themeMode: String, onThemeMode: (String) -> Unit,
    usageLimits: UsageLimits?, limitsLoading: Boolean, limitsError: String,
    onClose: () -> Unit, onSelectThread: (String) -> Unit,
    onDeleteThread: (String) -> Unit,
    onMoveThread: (String, String) -> Unit,
    actionError: String,
    onNewChat: (String?) -> Unit, onRefreshCatalog: () -> Unit,
    onRefreshLimits: () -> Unit,
    onResetLimits: (String) -> Unit, resetMessage: String, resetLoading: Boolean, resetPending: Boolean,
    onUpdates: () -> Unit, updateAvailable: Boolean,
    onDevices: () -> Unit,
    onDisconnect: () -> Unit,
    onGuests: () -> Unit = {},
    guestMode: Boolean = false,
) {
    val context = LocalContext.current
    fun chatCount(value: Int): String = when {
        value % 10 == 1 && value % 100 != 11 -> "$value чат"
        value % 10 in 2..4 && value % 100 !in 12..14 -> "$value чата"
        else -> "$value чатов"
    }
    BackHandler(onBack = onClose)
    var resetAttempt by remember { mutableStateOf<String?>(null) }
    resetAttempt?.let { attempt ->
        AlertDialog(onDismissRequest = { resetAttempt = null },
            title = { Text("Сбросить лимиты?") },
            text = { Text("Будет использован один доступный кредит сброса аккаунта. OpenAI определяет, какие окна лимитов можно сбросить. Действие нельзя отменить.") },
            confirmButton = { TextButton(onClick = { resetAttempt = null; onResetLimits(attempt) }) { Text("Использовать кредит") } },
            dismissButton = { TextButton(onClick = { resetAttempt = null }) { Text("Отмена") } })
    }
    var search by rememberSaveable { mutableStateOf("") }
    var searchFocused by remember { mutableStateOf(false) }
    var limitsExpanded by rememberSaveable { mutableStateOf(false) }
    val compactLimits = !limitsExpanded || searchFocused || search.isNotBlank() || WindowInsets.ime.getBottom(LocalDensity.current) > 0
    val visibleProjects = projects.map { group ->
        group to group.threads.filter { search.isBlank() ||
            group.name.contains(search, ignoreCase = true) || it.title.contains(search, ignoreCase = true) }
    }.filter { (group, matches) -> matches.isNotEmpty() || (group.id != "other" &&
        (search.isBlank() || group.name.contains(search, ignoreCase = true))) }
    var deleteCandidate by remember { mutableStateOf<ThreadItem?>(null) }
    var moveCandidate by remember { mutableStateOf<ThreadItem?>(null) }
    val expanded = rememberExpansionState()
    var settingsOpen by rememberSaveable { mutableStateOf(false) }
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
    if (settingsOpen) ModalBottomSheet(onDismissRequest = { settingsOpen = false }) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(UiSpace.screen)) {
            Text("Настройки", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(bottom = 16.dp))
                Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant,
                    RoundedCornerShape(16.dp)).padding(4.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf("system" to "Система", "light" to "Светлая", "dark" to "Тёмная").forEach { (mode, label) ->
                        val isSelected = themeMode == mode
                        Surface(
                            modifier = Modifier.weight(1f).heightIn(min = 48.dp).semantics {
                                selected = isSelected
                                contentDescription = "Тема: $label"
                                role = Role.RadioButton
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
                FlowRow(Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = onRefreshCatalog, enabled = !catalogLoading, modifier = Modifier.size(48.dp)
                        .semantics { contentDescription = "Обновить список" }) {
                        UiGlyph(UiIcon.Refresh, size = 20.dp)
                    }
                    TextButton(onClick = { settingsOpen = false; onUpdates() }) { Text(if (updateAvailable) "Обновить приложение" else "Обновления") }
                    TextButton(onClick = { settingsOpen = false; onGuests() }) { Text(if(guestMode) "Общие ресурсы" else "Гостевой доступ") }
                    if(!guestMode) TextButton(onClick = { settingsOpen = false; onDevices() }) { Text("ADB") }
                    TextButton(onClick = { settingsOpen = false; onDisconnect() }) { Text("Отключить") }
                }
        }
    }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            UiScreenHeader("Проекты", onClose, backDescription = "Назад к чату",
                subtitle = "Проектов: ${projects.count { it.id != "other" }} · Чатов: ${projects.sumOf { it.threads.size }}",
                actions = { IconButton(onClick = { onNewChat(null) }) { UiGlyph(UiIcon.Plus, "Новый чат", 23.dp) } })
        },
        bottomBar = {
            Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)
                .imePadding().navigationBarsPadding().padding(horizontal = UiSpace.screen, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { settingsOpen = true }, modifier = Modifier.weight(1f).heightIn(min = 48.dp)) {
                    Text("Настройки", modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    UiGlyph(UiIcon.ChevronRight)
                }
                IconButton(onClick = onRefreshCatalog, enabled = !catalogLoading) {
                    UiGlyph(UiIcon.Refresh, "Обновить список")
                }
            }
        },
    ) { inner ->
        LazyColumn(Modifier.fillMaxSize().consumeWindowInsets(inner),
            contentPadding = PaddingValues(top = inner.calculateTopPadding(),
                bottom = inner.calculateBottomPadding() + 20.dp)) {
            item(key = "search") {
                Surface(Modifier.fillMaxWidth().padding(horizontal = UiSpace.screen, vertical = 10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(12.dp)) {
                    BasicTextField(search, { search = it }, singleLine = true,
                        modifier = Modifier.fillMaxWidth().onFocusChanged { searchFocused = it.isFocused }
                            .semantics { contentDescription = "Поиск проектов и чатов" }
                            .padding(start = 14.dp, end = if (search.isBlank()) 14.dp else 4.dp, top = 6.dp, bottom = 6.dp),
                        textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.secondary),
                        decorationBox = { innerField ->
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                UiGlyph(UiIcon.Search, size = 18.dp,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.width(8.dp))
                                Box(Modifier.weight(1f).padding(vertical = 7.dp)) {
                                    if (search.isEmpty()) Text("Поиск проектов и чатов",
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        style = MaterialTheme.typography.bodyMedium)
                                    innerField()
                                }
                                if (search.isNotBlank()) TextButton(onClick = { search = "" },
                                    modifier = Modifier.size(48.dp).semantics { contentDescription = "Очистить поиск" }) {
                                    UiGlyph(UiIcon.Close, size = 18.dp)
                                }
                            }
                        })
                }
            }
            if (catalogLoading) item(key = "catalog-loading") {
                Column(Modifier.padding(horizontal = UiSpace.screen, vertical = 8.dp)) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                    Text("Обновляю проекты и чаты…", Modifier.padding(top = 8.dp),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (catalogError.isNotBlank()) item(key = "catalog-error") {
                Column(Modifier.padding(horizontal = UiSpace.screen, vertical = 8.dp)) {
                    Text(catalogError, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    TextButton(onClick = onRefreshCatalog, enabled = !catalogLoading) { Text("Повторить") }
                }
            }
            item(key = "limits") {
                if (actionError.isNotBlank()) Text(actionError,
                    Modifier.padding(horizontal = UiSpace.screen, vertical = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
                if(guestMode) Column(Modifier.fillMaxWidth().padding(horizontal=UiSpace.screen,vertical=12.dp)) {
                    Text("Выделенная квота",style=MaterialTheme.typography.titleMedium)
                    Text(limitsError,style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if(!guestMode) Surface(Modifier.fillMaxWidth().padding(horizontal = UiSpace.screen, vertical = 4.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(18.dp)) {
                    Column(Modifier.padding(horizontal = 16.dp, vertical = if (compactLimits) 0.dp else 13.dp)) {
                        if (compactLimits) {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text(if (usageLimits != null)
                                    "Осталось · 5ч ${usageLimits.fiveHours?.remainingPercent?.let { "$it%" } ?: "—"} · " +
                                        "неделя ${usageLimits.week?.remainingPercent?.let { "$it%" } ?: "—"}"
                                    else "Лимиты · " + if (limitsLoading) "получаю данные…" else limitsError.ifBlank { "данные недоступны" },
                                    Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                IconButton(onClick = { limitsExpanded = true }, modifier = Modifier.semantics {
                                    contentDescription = "Показать подробности лимитов"
                                }) { UiGlyph(UiIcon.ChevronDown, size = 18.dp) }
                            }
                        } else {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween) {
                                Text("Лимиты · осталось", style = MaterialTheme.typography.titleSmall,
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
                            if(!guestMode) TextButton(onClick = { resetAttempt = java.util.UUID.randomUUID().toString() },
                                enabled = !resetLoading && (resetPending || (usageLimits?.resetCredits ?: 0) > 0)) {
                                Text(if (resetLoading) "Сбрасываю…" else if (resetPending) "Проверить результат сброса" else "Сбросить лимиты · кредитов: ${usageLimits?.resetCredits?.toString() ?: "нет данных"}")
                            }
                            if (resetMessage.isNotBlank()) Text(resetMessage,
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (usageLimits != null && limitsError.isNotBlank()) Text(limitsError,
                                Modifier.padding(top = 5.dp), style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
            if (!catalogLoading && catalogError.isBlank() && visibleProjects.isEmpty()) item(key = "empty") {
                Column(Modifier.padding(horizontal = UiSpace.screen, vertical = 20.dp)) {
                    Text(if (search.isNotBlank()) "Ничего не найдено" else "Пока нет проектов и чатов",
                        style = MaterialTheme.typography.titleSmall)
                    Text(if (search.isNotBlank()) "Попробуйте другое название проекта или чата."
                        else "Создайте чат, чтобы начать работу.", Modifier.padding(top = 8.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (search.isNotBlank()) TextButton(onClick = { search = "" }) { Text("Очистить поиск") }
                    else TextButton(onClick = { onNewChat(null) }) { Text("Новый чат") }
                }
            }
            visibleProjects.forEach { (group, matches) ->
                val isStandalone = group.id == "other"
                val isCurrent = group.threads.any { it.id == selectedThreadId }
                val isExpanded = isStandalone || search.isNotBlank() || (expanded[group.id] ?: isCurrent)
                item(key = "heading:${group.id}") {
                    if (isStandalone) {
                        Text("Чаты", Modifier.padding(start = UiSpace.screen, top = 23.dp, bottom = 9.dp),
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else {
                        Row(Modifier.fillMaxWidth().clickable { expanded[group.id] = !isExpanded }
                            .padding(horizontal = UiSpace.screen, vertical = 11.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            UiGlyph(UiIcon.Folder, size = 24.dp,
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
                        var actionsOpen by rememberSaveable(thread.id) { mutableStateOf(false) }
                        Box {
                            if(!guestMode) IconButton(onClick = { actionsOpen = true }) { UiGlyph(UiIcon.More, "Действия чата: ${thread.title}", 22.dp) }
                            DropdownMenu(expanded = actionsOpen, onDismissRequest = { actionsOpen = false }) {
                                DropdownMenuItem(text = { Text("Переместить чат") }, leadingIcon = { UiGlyph(UiIcon.Folder) },
                                    onClick = { actionsOpen = false; moveCandidate = thread })
                                DropdownMenuItem(text = { Text("Удалить чат", color = MaterialTheme.colorScheme.error) },
                                    leadingIcon = { UiGlyph(UiIcon.Trash) }, onClick = { actionsOpen = false; deleteCandidate = thread })
                            }
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
