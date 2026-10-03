package ru.wilmain.codexphone

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.selection.selectable
import androidx.compose.ui.semantics.Role
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

internal fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun TaskControls(threadId: String, activeTurnId: String,
    request: suspend (String, JSONObject?) -> JSONObject, onChanges: () -> Unit,
    onInterrupted: () -> Unit = {}) {
    var prompts by remember(threadId) { mutableStateOf(emptyList<JSONObject>()) }
    var error by remember(threadId) { mutableStateOf("") }
    var sheet by rememberSaveable(threadId) { mutableStateOf(false) }
    var confirmStop by rememberSaveable(threadId) { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(threadId, refresh) {
        while (true) {
            try {
                prompts = (request("requests?threadId=${Uri.encode(threadId)}", null).optJSONArray("requests") ?: JSONArray()).objects()
                if (prompts.isEmpty()) sheet = false
            } catch (cancel: CancellationException) { throw cancel }
            catch (failure: Exception) { if (sheet) error = "Не удалось обновить: ${failure.message}" }
            delay(3000)
        }
    }
    if (activeTurnId.isNotBlank()) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(if (prompts.isEmpty()) "Работа выполняется" else "Codex ожидает ответа",
            Modifier.weight(1f).padding(vertical = 14.dp), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = { confirmStop = activeTurnId }, enabled = !busy) { Text("Остановить") }
    }
    if (prompts.isNotEmpty()) TextButton(onClick = { sheet = true }, modifier = Modifier.fillMaxWidth()) {
        Text("Требуется ответ · ${prompts.size}", fontWeight = FontWeight.SemiBold)
    }
    if (error.isNotBlank() && !sheet) Text(error, color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodySmall)
    if (confirmStop != null) AlertDialog(onDismissRequest = { if (!busy) confirmStop = null },
        title = { Text("Остановить задачу?") },
        text = { Text("Текущая работа будет прервана. Уже внесённые изменения останутся. Сообщения в очереди не удаляются.") },
        confirmButton = { TextButton(enabled = !busy, onClick = {
            val turn = confirmStop ?: return@TextButton
            busy = true
            scope.launch {
                try {
                    request("turn/interrupt", JSONObject().put("threadId", threadId).put("turnId", turn))
                    confirmStop = null; error = ""; onInterrupted()
                } catch (cancel: CancellationException) { throw cancel }
                catch (failure: Exception) { error = "Не удалось остановить: ${failure.message}"; confirmStop = null }
                finally { busy = false }
            }
        }) { Text(if (busy) "Останавливаю…" else "Остановить") } },
        dismissButton = { TextButton(enabled = !busy, onClick = { confirmStop = null }) { Text("Продолжить работу") } })
    if (sheet) ModalBottomSheet(onDismissRequest = { if (!busy) sheet = false }) {
        Column(Modifier.fillMaxWidth().imePadding().verticalScroll(rememberScrollState())
            .padding(horizontal = UiSpace.screen, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Ответ для Codex", style = MaterialTheme.typography.headlineSmall)
            if (prompts.firstOrNull()?.optString("threadId")?.let { it.isNotBlank() && it != threadId } == true)
                Text("Запрос субагента", style = MaterialTheme.typography.bodySmall)
            if (error.isNotBlank()) {
                Text(error, color = MaterialTheme.colorScheme.error)
                TextButton(onClick = { refresh++ }) { Text("Обновить запросы") }
            }
            prompts.firstOrNull()?.let { prompt ->
                RequestBody(prompt, busy, onChanges) { response ->
                    busy = true
                    scope.launch {
                        try {
                            request("requests/respond", JSONObject().put("threadId", prompt.optString("threadId").ifBlank { threadId })
                                .put("id", prompt.getString("id")).put("response", response))
                            prompts = prompts.filter { it.optString("id") != prompt.optString("id") }
                            sheet = prompts.isNotEmpty(); error = ""; refresh++
                        } catch (cancel: CancellationException) { throw cancel }
                        catch (failure: Exception) { error = "Ответ не подтверждён: ${failure.message}. Обновите запросы или повторите ответ." }
                        finally { busy = false }
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
internal fun RequestBody(prompt: JSONObject, busy: Boolean, onChanges: () -> Unit,
    respond: (JSONObject) -> Unit) {
    val method = prompt.optString("method")
    val params = prompt.optJSONObject("params") ?: JSONObject()
    val id = prompt.optString("id")
    if (method == "item/tool/requestUserInput") {
        var answersText by rememberSaveable(id) { mutableStateOf("{}") }
        val answers = JSONObject(answersText)
        val questions = (params.optJSONArray("questions") ?: JSONArray()).objects()
        questions.forEach { question ->
            val qid = question.optString("id")
            val secret = question.optBoolean("isSecret")
            Text(question.optString("question"), style = MaterialTheme.typography.titleMedium)
            fun setAnswer(value: String) { answersText = JSONObject(answersText).put(qid, value.take(32000)).toString() }
            val options = (question.optJSONArray("options") ?: JSONArray()).objects()
            options.forEach { option ->
                val label = option.optString("label")
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).selectable(
                    selected = answers.optString(qid) == label, enabled = !busy,
                    role = Role.RadioButton, onClick = { setAnswer(label) }),
                    verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    RadioButton(selected = answers.optString(qid) == label, enabled = !busy, onClick = null)
                    Column(Modifier.weight(1f).padding(vertical = 8.dp)) {
                        Text(label, style = MaterialTheme.typography.bodyLarge)
                        if (option.optString("description").isNotBlank()) Text(option.optString("description"),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (options.isEmpty() || question.optBoolean("isOther")) OutlinedTextField(
                value = answers.optString(qid), onValueChange = ::setAnswer,
                label = { Text(if (secret) "Конфиденциальный ответ" else "Ваш ответ") },
                modifier = Modifier.fillMaxWidth(), enabled = !busy, maxLines = if (secret) 1 else 4,
                visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None)
        }
        Button(enabled = !busy && questions.isNotEmpty() && questions.all { answers.optString(it.optString("id")).isNotBlank() },
            onClick = {
                val result = JSONObject()
                questions.forEach { q -> result.put(q.optString("id"), JSONObject().put("answers", JSONArray().put(answers.optString(q.optString("id"))))) }
                respond(JSONObject().put("answers", result))
            }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(if (busy) "Отправляю…" else "Отправить ответ") }
    } else {
        val permissions = method == "item/permissions/requestApproval"
        val fileChange = method.contains("fileChange") || method == "applyPatchApproval"
        Text(when { permissions -> "Дополнительные разрешения"; fileChange -> "Изменение файлов"; else -> "Выполнение команды" },
            style = MaterialTheme.typography.titleLarge)
        params.optString("reason").takeUnless { it.isBlank() || it == "null" }?.let { Text(it) }
        val command = params.opt("command")
        if (command != null && command != JSONObject.NULL) SelectionContainer {
            Text(if (command is JSONArray) (0 until command.length()).joinToString(" ") { command.optString(it) } else command.toString(),
                fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodyMedium)
        }
        params.optString("cwd").takeUnless { it.isBlank() || it == "null" }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        if (permissions) {
            val profile = params.optJSONObject("permissions") ?: JSONObject()
            val network = profile.optJSONObject("network")
            Text("Сеть: ${if (network?.optBoolean("enabled") == true) "запрошен доступ" else "не запрошена"}")
            profile.optJSONObject("fileSystem")?.let { fs ->
                Text("Файловая система", fontWeight = FontWeight.SemiBold)
                SelectionContainer { Text(fs.toString(2), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
            }
        }
        params.optJSONObject("additionalPermissions")?.let { Text("Также запрошены разрешения:")
            SelectionContainer { Text(it.toString(2), fontFamily = FontFamily.Monospace) } }
        if (fileChange) TextButton(onClick = onChanges) { Text("Посмотреть изменения") }
        Text(if (permissions) "Разрешение действует только на эту задачу." else "Разрешение действует только для этого запроса.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        val advertised = params.optJSONArray("availableDecisions")
        val acceptAllowed = advertised == null || (0 until advertised.length()).any { advertised.optString(it) == "accept" }
        val declineAllowed = advertised == null || (0 until advertised.length()).any { advertised.optString(it) == "decline" }
        if (acceptAllowed) Button(enabled = !busy, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            onClick = { respond(JSONObject().put("decision", "accept")) }) { Text(if (permissions) "Разрешить на эту задачу" else "Разрешить один раз") }
        if (declineAllowed) OutlinedButton(enabled = !busy, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            onClick = { respond(JSONObject().put("decision", "decline")) }) { Text("Отклонить") }
        if (advertised != null && !acceptAllowed && !declineAllowed) Text("Для запроса требуется специальное решение. Ответьте в Desktop.", color = MaterialTheme.colorScheme.error)
    }
}

@Composable
internal fun ChangesScreen(threadId: String, request: suspend (String, JSONObject?) -> JSONObject,
    onClose: () -> Unit, onFile: (String) -> Unit) {
    var changes by remember(threadId) { mutableStateOf(emptyList<JSONObject>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var truncated by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    val expanded = rememberExpansionState()
    val clipboard = LocalClipboardManager.current
    LaunchedEffect(threadId, refresh) {
        loading = true; error = ""
        try {
            val result = request("changes?threadId=${Uri.encode(threadId)}", null)
            changes = (result.optJSONArray("changes") ?: JSONArray()).objects()
            truncated = result.optBoolean("truncated")
        } catch (cancel: CancellationException) { throw cancel }
        catch (failure: Exception) { error = "Не удалось загрузить изменения: ${failure.message}" }
        finally { loading = false }
    }
    Scaffold(topBar = { UiScreenHeader("Изменения", onClose, subtitle = "Из истории задач этого чата",
        actions = { IconButton(onClick = { refresh++ }, enabled = !loading) { UiGlyph(UiIcon.Refresh, "Обновить изменения") } }) }) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(UiSpace.screen),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (loading) item { CircularProgressIndicator(Modifier.size(24.dp)) }
            if (error.isNotBlank()) item { Text(error, color = MaterialTheme.colorScheme.error); TextButton(onClick = { refresh++ }) { Text("Повторить") } }
            if (!loading && error.isBlank() && changes.isEmpty()) item { Text("Изменений файлов в истории пока нет") }
            if (truncated) item { Text("Показана часть изменений. Полный diff доступен на ПК.", style = MaterialTheme.typography.bodySmall) }
            items(changes, key = { it.optString("id") }) { change ->
                val id = change.optString("id")
                val diff = change.optString("diff")
                val add = diff.lineSequence().count { it.startsWith("+") && !it.startsWith("+++") }
                val remove = diff.lineSequence().count { it.startsWith("-") && !it.startsWith("---") }
                Column {
                    TextButton(onClick = { expanded[id] = !(expanded[id] ?: false) }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                        Text(change.optString("path"), Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                        Text("+$add −$remove", style = MaterialTheme.typography.labelLarge)
                        Spacer(Modifier.width(8.dp))
                        UiGlyph(if (expanded[id] == true) UiIcon.ChevronUp else UiIcon.ChevronDown, "Раскрыть diff")
                    }
                    val state = when (change.optString("status")) {
                        "completed" -> "Применено"; "failed" -> "Ошибка применения"; "declined" -> "Отклонено"; else -> "В процессе"
                    }
                    Text(state, Modifier.padding(horizontal = 12.dp), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (expanded[id] == true) {
                        Row { TextButton(onClick = { onFile(change.optString("path")) }) { Text("Открыть папку") }
                            TextButton(onClick = { clipboard.setText(AnnotatedString(diff)) }) { Text("Копировать diff") } }
                        if (change.optBoolean("truncated")) Text("Diff сокращён", color = MaterialTheme.colorScheme.error)
                        DiffContent(diff)
                    }
                }
            }
        }
    }
}

@Composable
internal fun DiffContent(diff: String) {
    // Single selectable block avoids creating thousands of individual Compose nodes.
    val addedColor = MaterialTheme.colorScheme.secondary
    val removedColor = MaterialTheme.colorScheme.error
    val annotated = remember(diff, addedColor, removedColor) { androidx.compose.ui.text.buildAnnotatedString {
        diff.lineSequence().forEach { line ->
            val color = when {
                line.startsWith("+") && !line.startsWith("+++") -> addedColor
                line.startsWith("-") && !line.startsWith("---") -> removedColor
                else -> null
            }
            if (color != null) pushStyle(androidx.compose.ui.text.SpanStyle(color = color))
            append(line); append('\n')
            if (color != null) pop()
        }
    } }
    SelectionContainer { Text(annotated, Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant)
        .padding(12.dp), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall) }
}
