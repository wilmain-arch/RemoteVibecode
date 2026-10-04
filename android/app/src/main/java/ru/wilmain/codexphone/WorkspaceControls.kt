package ru.wilmain.codexphone

import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.Saver
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

private data class ControlForm(val action: String, val title: String, val note: String,
    val fields: List<Pair<String,String>> = emptyList(), val extra: JSONObject = JSONObject())

private val ControlFormSaver = Saver<ControlForm?, String>(
    save = { form -> if (form == null) "" else JSONObject().put("action", form.action)
        .put("title", form.title).put("note", form.note).put("fields", JSONArray(form.fields.map { JSONArray(listOf(it.first, it.second)) }))
        .put("extra", form.extra).toString() },
    restore = { text -> if (text.isBlank()) null else JSONObject(text).let { obj ->
        val array = obj.getJSONArray("fields")
        ControlForm(obj.getString("action"), obj.getString("title"), obj.getString("note"),
            (0 until array.length()).map { array.getJSONArray(it).let { pair -> pair.getString(0) to pair.getString(1) } }, obj.getJSONObject("extra"))
    } }
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WorkspaceControls(threadId: String, title: String,
    initialTurn: String = "", request: suspend (String, JSONObject?) -> JSONObject,
    onClose: () -> Unit, onSelect: (String) -> Unit,
    onJump: (String) -> Unit, onChanged: () -> Unit, compatibilityNote: String = "", onProjectFile: (String) -> Unit = {}) {
    if(compatibilityNote.isNotBlank()) {
        Scaffold(topBar={UiScreenHeader(title="Инструменты чата",subtitle=title,onBack=onClose)}) { padding ->
            Column(Modifier.padding(padding).padding(24.dp)) {
                Text(compatibilityNote, color=MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick=onChanged, modifier=Modifier.heightIn(min=48.dp)) { Text("Повторить проверку") }
            }
        }
        return
    }
    var section by rememberSaveable(threadId) { mutableStateOf("chat") }
    var query by rememberSaveable(threadId) { mutableStateOf("") }
    var data by remember(threadId, section) { mutableStateOf(JSONObject()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var notice by remember { mutableStateOf("") }
    var revision by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var form by rememberSaveable(stateSaver=ControlFormSaver) { mutableStateOf<ControlForm?>(null) }
    var fields by rememberSaveable { mutableStateOf("{}") }
    val scope = rememberCoroutineScope()
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    BackHandler { if (!busy) onClose() }
    val sections = listOf("chat" to "Чат", "search" to "Поиск", "archive" to "Архив",
        "plan" to "План и цель", "queue" to "Очередь Desktop", "projects" to "Проекты", "git" to "Git")
    fun open(value: ControlForm) {
        val extra = JSONObject(value.extra.toString())
        fields = extra.optJSONObject("_fields")?.toString() ?: "{}"
        extra.remove("_fields")
        form = value.copy(extra=extra.put("operationId", UUID.randomUUID().toString()))
        error = ""
    }
    fun execute(action: String, body: JSONObject) {
        if (busy) return
        busy = true; error = ""
        scope.launch {
            try {
                val result = request("control/action", body.put("threadId", threadId).put("action", action))
                form = null; revision++; notice = "Готово"; onChanged()
                if (action in listOf("fork", "archive") && result.optString("threadId").isNotBlank()) onSelect(result.optString("threadId"))
                if (action == "plan-create") {
                    if (result.optString("threadId").isNotBlank() && result.optString("threadId") != threadId) onSelect(result.optString("threadId"))
                    onClose()
                }
                if (action == "review") { notice = "Ревью запущено. Результат появится в чате"; onClose() }
            } catch (cancel: CancellationException) { throw cancel }
            catch (e: Exception) { error = e.message ?: "Не удалось выполнить действие" }
            finally { busy = false }
        }
    }
    LaunchedEffect(threadId, section, query, revision) {
        error = ""; data = JSONObject()
        if (section == "chat" || section == "search" && query.isBlank()) { loading = false; return@LaunchedEffect }
        loading = true
        try {
            if (section == "search") delay(350)
            data = request("control?threadId=${Uri.encode(threadId)}&section=$section&query=${Uri.encode(query)}", null)
        } catch (cancel: CancellationException) { throw cancel }
        catch (e: Exception) { error = e.message ?: "Не удалось загрузить" }
        finally { loading = false }
    }
    Scaffold(topBar = { UiScreenHeader(title = "Инструменты чата", subtitle = title, onBack = onClose,
        actions = { IconButton(onClick={revision++}) { UiGlyph(UiIcon.Refresh, "Обновить раздел") } }) },
        contentWindowInsets = WindowInsets.safeDrawing) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding).imePadding()) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
                sections.forEach { (id, label) ->
                    FilterChip(selected = section == id, onClick = { if (!busy) { section=id; notice="" } },
                        label = { Text(label) }, border = null, modifier = Modifier.padding(end = 8.dp).heightIn(min=48.dp))
                }
            }
            LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                if (section == "search") item {
            if (section == "search") TextField(query, { query=it }, label={Text("Поиск сообщений в этом чате")},
                modifier=Modifier.fillMaxWidth(), singleLine=true,
                keyboardOptions=KeyboardOptions(imeAction=ImeAction.Search),
                keyboardActions=KeyboardActions(onSearch={keyboard?.hide();focus.clearFocus();revision++}))
                }
                if (notice.isNotBlank()) item { Text(notice, color=MaterialTheme.colorScheme.secondary) }
                if (error.isNotBlank() && form == null) item {
                    Text(error, color=MaterialTheme.colorScheme.error)
                    TextButton(onClick={keyboard?.hide();focus.clearFocus();revision++}, enabled=!busy) { Text("Повторить") }
                }
                if (loading) item { LinearProgressIndicator(Modifier.fillMaxWidth()); Text("Загрузка…") }
                if (data.optString("note").isNotBlank()) item { Text(data.optString("note")) }
                when (section) {
                    "chat" -> {
                        item { Text("Управление чатом", style=MaterialTheme.typography.headlineSmall) }
                        item { TextButton(onClick={open(ControlForm("rename","Название чата","Название видно в списке проектов",listOf("name" to "Название"),extra=JSONObject().put("_fields",JSONObject().put("name",title))))}) {Text("Переименовать") } }
                        item { TextButton(onClick={open(ControlForm("archive","Архивировать чат?","Чат исчезнет из обычного списка. Восстановить его можно в разделе «Архив». Файлы останутся."))}) {Text("Архивировать")} }
                        if (initialTurn.isNotBlank()) {
                            item { Text("Выбранный ход", style=MaterialTheme.typography.titleMedium) }
                            item { TextButton(onClick={open(ControlForm("fork","Создать ветку?","Новый чат включит историю до выбранного хода включительно. Исходный чат не изменится.",extra=JSONObject().put("turnId",initialTurn)))}) {Text("Создать ветку отсюда")} }
                            item { TextButton(onClick={open(ControlForm("revert","Откатить историю?","Выбранный ход и последующие сообщения будут удалены из истории. Изменения файлов НЕ откатываются. Действие необратимо.",extra=JSONObject().put("turnId",initialTurn)))}) {Text("Откатить до этого хода")} }
                        } else item { Text("Для ветки или отката откройте меню сообщения в ленте.", color=MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                    "search" -> {
                        val results=(data.optJSONArray("data")?:JSONArray()).objects()
                        if (!loading && error.isBlank() && query.isNotBlank() && results.isEmpty()) item { Text("Совпадений нет") }
                        items(results) { hit ->
                            Text(title, style=MaterialTheme.typography.labelMedium, color=MaterialTheme.colorScheme.onSurfaceVariant)
                            SelectionContainer { Text(hit.optString("snippet"), style=MaterialTheme.typography.bodyLarge) }
                            TextButton(onClick={keyboard?.hide();focus.clearFocus();onJump(hit.optString("turnId"));onClose()}) {Text("Открыть сообщение")}
                        }
                    }
                    "archive" -> {
                        val threads=(data.optJSONArray("data")?:JSONArray()).objects()
                        if (!loading && error.isBlank() && threads.isEmpty()) item { Text("Архив пуст") }
                        items(threads) { t ->
                            Text(t.optString("name").takeIf { it.isNotBlank() && it!="null" }?:t.optString("preview","Чат"),style=MaterialTheme.typography.titleMedium)
                            TextButton(onClick={execute("restore",JSONObject().put("targetId",t.optString("id")))}, enabled=!busy) {Text("Восстановить")}
                        }
                    }
                    "plan" -> {
                        item { Text("Режим следующего сообщения",style=MaterialTheme.typography.titleMedium) }
                        items((data.optJSONArray("modes")?:JSONArray()).objects()) { mode ->
                            val id=mode.optString("mode")
                            FilterChip(selected=data.optString("selectedMode")==id,enabled=!busy,
                                onClick={execute("mode",JSONObject().put("mode",id))},label={Text(if(id=="plan") "Планирование" else if(id=="default") "Обычный" else mode.optString("name",id))})
                        }
                        item {
                            Button(onClick={open(ControlForm("plan-create", "Создать новый план", "Опишите задачу. Codex составит новый план в этом чате. Запуск расходует лимиты; предыдущие планы сохранятся в истории.", listOf("text" to "Что нужно распланировать")))},
                                enabled=!busy && !loading && (data.optJSONArray("modes")?:JSONArray()).objects().any { it.optString("mode")=="plan" },
                                modifier=Modifier.heightIn(min=48.dp)) { Text("Создать новый план") }
                        }
                        val published=(data.optJSONArray("publishedPlans")?:JSONArray()).objects()
                        val plans=(data.optJSONArray("plans")?:JSONArray()).objects()
                        val current=data.optString("currentPlanTurnId").takeUnless { it.isBlank() || it=="null" }
                            ?: plans.lastOrNull()?.optString("turnId") ?: published.lastOrNull()?.optString("turnId")
                        item { Text("Текущий план", style=MaterialTheme.typography.titleLarge) }
                        val currentPlans=plans.filter { it.optString("turnId")==current }
                        val currentPublished=published.filter { it.optString("turnId")==current }
                        if (currentPlans.isEmpty() && currentPublished.isEmpty() && !loading && error.isBlank()) item {
                            Text(if(current==null) "План ещё не создан" else if(data.optString("currentPlanStatus") in listOf("inProgress", "active"))
                                "Codex составляет новый план. Ход работы доступен в чате." else "В этом ходе план не опубликован. Посмотрите ответ в чате.")
                        }
                        items(currentPlans) { plan -> SelectionContainer { MarkdownContent(plan.optString("text"), onProjectFile = onProjectFile) } }
                        items(currentPublished) { plan -> PublishedPlanContent(plan, onProjectFile) }
                        val goal=data.optJSONObject("goal")
                        item { Text("Цель",style=MaterialTheme.typography.titleMedium) }
                        if (goal != null) {
                            item { Text(goal.optString("objective"));Text("${when(goal.optString("status")){"paused"->"Приостановлена";"active"->"Выполняется";"complete"->"Завершена";"blocked"->"Нужна помощь";"budgetLimited"->"Бюджет исчерпан";"usageLimited"->"Лимит исчерпан";else->"Состояние неизвестно"}} · токенов: ${goal.optLong("tokensUsed")}",style=MaterialTheme.typography.bodySmall) }
                            item { TextButton(onClick={open(ControlForm("goal-status","Возобновить цель?","Codex может сразу продолжить работу и расходовать лимиты.",extra=JSONObject().put("status","active")))},enabled=!busy){Text("Возобновить цель")} }
                            item { Row { TextButton(onClick={execute("goal-status",JSONObject().put("status","paused"))},enabled=!busy) {Text("Приостановить")};TextButton(onClick={execute("goal-status",JSONObject().put("status","complete"))},enabled=!busy) {Text("Завершить")} } }
                            item { TextButton(onClick={open(ControlForm("goal-clear","Удалить цель?","История чата и файлы останутся."))}) {Text("Удалить цель")} }
                        }
                        item { TextButton(onClick={open(ControlForm("goal-set","Задать цель","Цель будет создана приостановленной. Для продолжения нажмите «Возобновить цель». Бюджет токенов необязателен.",listOf("objective" to "Цель", "tokenBudget" to "Бюджет токенов (необязательно)"),extra=JSONObject().put("_fields",JSONObject().put("objective",goal?.optString("objective").orEmpty()).put("tokenBudget",goal?.opt("tokenBudget")?.takeUnless{it==JSONObject.NULL}?.toString().orEmpty()))))}) {Text(if(goal==null) "Задать цель" else "Изменить цель")} }
                    }
                    "queue" -> {
                        item { Text("Очередь Desktop",style=MaterialTheme.typography.titleLarge);Text("Сообщение может запуститься сразу, если чат свободен. Модель наследуется из настроек чата.",style=MaterialTheme.typography.bodySmall) }
                        val queued=(data.optJSONArray("data")?:JSONArray()).objects()
                        if (!loading && error.isBlank() && queued.isEmpty()) item {Text("Очередь Desktop пуста")}
                        items(queued) { msg ->
                            val text=(msg.optJSONArray("input")?:JSONArray()).objects().mapNotNull { it.optString("text").takeIf(String::isNotBlank) }.joinToString("\n")
                            Text(text.ifBlank {"Сообщение с вложением"})
                            Row(Modifier.horizontalScroll(rememberScrollState())) {
                                TextButton(onClick={open(ControlForm("queue-update","Изменить сообщение","В этой форме редактируется только текст. Сообщения с вложениями изменяйте на ПК.",listOf("text" to "Сообщение"),JSONObject().put("submissionId",msg.optString("id")).put("_fields",JSONObject().put("text",text))))},enabled=!busy && (msg.optJSONArray("input")?:JSONArray()).objects().all { it.optString("type")=="text" }) {Text("Изменить")}
                                TextButton(onClick={open(ControlForm("queue-delete","Удалить из очереди?","Уже запущенное сообщение этим действием не останавливается.",extra=JSONObject().put("submissionId",msg.optString("id"))))},enabled=!busy) {Text("Удалить")}
                                TextButton(onClick={open(ControlForm("queue-start","Запустить сообщение?","Выполнение расходует лимиты Codex.",extra=JSONObject().put("submissionId",msg.optString("id"))))},enabled=!busy) {Text("Запустить")}
                                if (queued.indexOf(msg)>0) TextButton(onClick={
                                    val ids=queued.map{it.optString("id")}.toMutableList();val at=ids.indexOf(msg.optString("id"));java.util.Collections.swap(ids,at,at-1)
                                    execute("queue-reorder",JSONObject().put("ids",JSONArray(ids)))
                                },enabled=!busy && data.isNull("nextCursor")) {Text("Выше")}
                            }
                        }
                        item { TextButton(onClick={open(ControlForm("queue-add","Добавить в очередь?","В свободном чате сообщение может начать выполняться сразу и расходовать лимиты.",listOf("text" to "Сообщение"),JSONObject().put("messageId",UUID.randomUUID().toString())))}) {Text("Добавить сообщение")} }
                        val legacy=(data.optJSONArray("bridgeQueue")?:JSONArray()).objects()
                        if(legacy.isNotEmpty()) item {Text("Отправка телефона через мост · ${legacy.size}. Отмена и корректировка доступны в ленте чата.",color=MaterialTheme.colorScheme.onSurfaceVariant)}
                    }
                    "projects" -> {
                        item { TextButton(onClick={open(ControlForm("project-create","Новый проект","Укажите существующую папку на ПК. Файлы не перемещаются.",listOf("name" to "Название","path" to "Абсолютный путь на ПК"),JSONObject().put("messageId",UUID.randomUUID().toString())))}) {Text("Создать проект")} }
                        item { TextButton(onClick={open(ControlForm("folder-create","Создать рабочую папку","Путь относительно текущей рабочей папки чата. Для нового проекта затем укажите её полный путь.",listOf("path" to "Имя папки")))}) {Text("Создать папку")} }
                        items((data.optJSONArray("data")?:JSONArray()).objects()) { project ->
                            Text(project.optString("name"),style=MaterialTheme.typography.titleMedium)
                            (project.optJSONArray("roots")?:JSONArray()).objects().forEach { Text(it.optString("path"),style=MaterialTheme.typography.bodySmall) }
                            Row {
                                TextButton(onClick={open(ControlForm("project-rename","Название проекта","Папка на диске не переименовывается.",listOf("name" to "Название"),JSONObject().put("projectId",project.optString("id")).put("_fields",JSONObject().put("name",project.optString("name")))))}) {Text("Переименовать")}
                                TextButton(onClick={open(ControlForm("project-delete","Удалить проект из списка?","Папка и файлы на ПК останутся. Чаты не удаляются.",extra=JSONObject().put("projectId",project.optString("id"))))}) {Text("Удалить")}
                            }
                        }
                    }
                    "git" -> {
                        if(data.has("branch")) {
                            item {Text("Ветка: ${data.optString("branch").ifBlank {"detached HEAD"}}",style=MaterialTheme.typography.titleLarge)}
                            item {SelectionContainer {Text(data.optString("status").ifBlank {"Рабочая папка чистая"})}}
                            item {TextButton(onClick={open(ControlForm("git-switch","Переключить ветку?","Переключение разрешено только при чистой рабочей папке.",listOf("branch" to "Существующая локальная ветка")))}) {Text("Переключить ветку")}}
                            item {Text((data.optJSONArray("branches")?:JSONArray()).let { a -> (0 until a.length()).joinToString(" · "){a.optString(it)} },style=MaterialTheme.typography.bodySmall)}
                            item {TextButton(onClick={open(ControlForm("git-stage","Подготовить все изменения?","Все изменения текущего репозитория, включая новые и удалённые файлы, будут добавлены в индекс. Коммит не создаётся."))}) {Text("Подготовить изменения")}}
                            item {TextButton(onClick={open(ControlForm("git-commit","Создать коммит?","Будут сохранены только подготовленные изменения. Push не выполняется. Локальные Git hooks не запускаются.",listOf("message" to "Сообщение коммита")))}) {Text("Создать коммит")}}
                            item {Text("Текущий Git diff",style=MaterialTheme.typography.titleMedium);DiffContent(data.optString("diff").ifBlank {"Отслеживаемые файлы не изменены"});if(data.optBoolean("truncated"))Text("Diff сокращён")}
                            item {Text("Worktrees",style=MaterialTheme.typography.titleMedium);SelectionContainer {Text(data.optString("worktrees"))}}
                            item {TextButton(onClick={open(ControlForm("git-worktree","Создать worktree?","Будет создана соседняя папка для существующей ветки. Занятая ветка не переносится принудительно.",listOf("name" to "Имя папки: латиница","branch" to "Существующая ветка")))}) {Text("Создать worktree")}}
                            item {TextButton(onClick={open(ControlForm("review","Запустить ревью?","Codex проверит незакоммиченные изменения. Это модельная задача, она расходует лимит. Результат появится в этом чате."))}) {Text("Запустить ревью")}}
                        }
                    }
                }
                val cursor=data.optString("nextCursor").takeIf { it.isNotBlank() && it!="null" }
                if(cursor!=null) item {
                    TextButton(enabled=!busy && !loading,onClick={
                        busy=true;val requestedSection=section;val requestedQuery=query;scope.launch {
                            try {
                                val page=request("control?threadId=${Uri.encode(threadId)}&section=$section&query=${Uri.encode(query)}&cursor=${Uri.encode(cursor)}",null)
                                val merged=JSONArray();(data.optJSONArray("data")?:JSONArray()).objects().forEach{merged.put(it)};(page.optJSONArray("data")?:JSONArray()).objects().forEach{merged.put(it)}
                                if (section == requestedSection && query == requestedQuery) data=JSONObject(page.toString()).put("data",merged)
                            }catch(c:CancellationException){throw c}catch(e:Exception){error=e.message?:"Ошибка загрузки"}finally{busy=false}
                        }
                    }){Text("Показать ещё")}
                }
                if(busy)item {LinearProgressIndicator(Modifier.fillMaxWidth())}
            }
        }
    }
    form?.let { f ->
        AlertDialog(onDismissRequest={if(!busy)form=null},title={Text(f.title)},text={
            Column(Modifier.heightIn(max=420.dp).verticalScrollCompat().imePadding(),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Text(f.note)
                f.fields.forEach { (key,label) ->
                    val values=JSONObject(fields)
                    OutlinedTextField(values.optString(key),{fields=JSONObject(fields).put(key,it).toString()},label={Text(label)},modifier=Modifier.fillMaxWidth(),enabled=!busy)
                }
                if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
            }
        },confirmButton={TextButton(enabled=!busy && f.fields.all{it.first == "tokenBudget" || JSONObject(fields).optString(it.first).isNotBlank()},onClick={
            val body=JSONObject(f.extra.toString());val v=JSONObject(fields);v.keys().forEach{body.put(it,v.get(it))};body.put("confirmed",true);execute(f.action,body)
        }){Text(if(busy)"Выполняется…" else if(f.action=="plan-create") "Создать план" else "Подтвердить")}},dismissButton={TextButton(enabled=!busy,onClick={form=null}){Text("Отмена")}})
    }
}

@Composable
private fun Modifier.verticalScrollCompat(): Modifier = this.then(Modifier.verticalScroll(rememberScrollState()))

@Composable
private fun PublishedPlanContent(plan: JSONObject, onProjectFile: (String) -> Unit) {
    if(plan.optString("explanation").isNotBlank()) MarkdownContent(plan.optString("explanation"), onProjectFile = onProjectFile)
    (plan.optJSONArray("steps")?:JSONArray()).objects().forEach { step ->
        val state=when(step.optString("status")){"completed"->"Готово";"inProgress"->"Выполняется";else->"Ожидает"}
        Text(state,style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.secondary)
        MarkdownContent(step.optString("step"), onProjectFile = onProjectFile)
    }
}
