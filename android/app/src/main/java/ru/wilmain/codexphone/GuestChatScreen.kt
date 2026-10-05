package ru.wilmain.codexphone

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID

internal fun guestTaskLabel(state:String)=when(state) {
    "queued"->"В очереди"
    "dispatching"->"Подготовка"
    "running"->"Codex работает"
    "cancel_requested"->"Останавливается"
    "completed"->"Готово"
    "cancelled"->"Отменено"
    "failed"->"Ошибка выполнения"
    "uncertain"->"Исход требует проверки владельцем"
    else->state
}

@Composable
internal fun GuestChatScreen(name:String, initialDraft:String, initialPending:String,
    onPersist:(String,String)->Unit, onClose:()->Unit, initialScope:String="default", onScope:(String)->Unit={}, onThemeChanged:()->Unit={}, onRequest:suspend(String,JSONObject?)->JSONObject) {
    val scope=rememberCoroutineScope()
    val context=androidx.compose.ui.platform.LocalContext.current
        var draftStore by rememberSaveable {mutableStateOf(runCatching { JSONObject(initialDraft).takeIf {it.optInt("version")==1}?.toString() }.getOrNull() ?: JSONObject().put("version",1).put("drafts",JSONObject().put("$initialScope/main",initialDraft)).toString())}
    var draft by rememberSaveable {mutableStateOf("")}
    var pending by rememberSaveable {mutableStateOf(initialPending)}
    var tasks by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var nextCursor by remember {mutableStateOf("")}
    var olderLoaded by remember {mutableStateOf(false)}
    var error by remember {mutableStateOf("")}
    var busy by remember {mutableStateOf(false)}
    var currentScope by rememberSaveable {mutableStateOf(initialScope)}
    var shared by rememberSaveable {mutableStateOf(false)}
    var files by rememberSaveable {mutableStateOf(false)}
    var filePath by rememberSaveable {mutableStateOf("")}
    var conversation by rememberSaveable {mutableStateOf("main")}
    fun persist() {
        val store=JSONObject(draftStore)
        store.getJSONObject("drafts").put("$currentScope/$conversation",draft)
        store.put("scope",currentScope).put("conversation",conversation)
        draftStore=store.toString();onPersist(draftStore,pending)
    }
    fun selectChat(nextScope:String,nextConversation:String) {
        persist();currentScope=nextScope;conversation=nextConversation
        draft=JSONObject(draftStore).getJSONObject("drafts").optString("$currentScope/$conversation")
        persist();onScope(currentScope)
    }
    LaunchedEffect(Unit) {
        val store=JSONObject(draftStore)
        val outbox=runCatching {JSONObject(pending)}.getOrNull()
        currentScope=outbox?.optString("scopeId",initialScope) ?: store.optString("scope",initialScope)
        conversation=outbox?.optString("conversationId","main") ?: store.optString("conversation","main")
        draft=store.getJSONObject("drafts").optString("$currentScope/$conversation")
    }
    var models by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var model by rememberSaveable {mutableStateOf("")}
    var effort by rememberSaveable {mutableStateOf("")}
    var guest by remember {mutableStateOf<JSONObject?>(null)}
    suspend fun refresh() {
        guest=onRequest("guest/self",null)
        val response=onRequest("guest/tasks",null)
        val rows=response.getJSONArray("tasks")
        val latest=(0 until rows.length()).map {rows.getJSONObject(it)}.reversed()
        val ids=latest.map {it.getString("operationId")}.toSet()
        tasks=tasks.filter {it.getString("operationId") !in ids}+latest
        if(!olderLoaded) nextCursor=response.optString("nextCursor").takeUnless {it=="null"}.orEmpty()
        if(pending.isNotBlank() && tasks.any {it.optString("operationId")==JSONObject(pending).optString("operationId")}) {
            pending="";draft="";persist()
        }
    }
    var downloadSpec by rememberSaveable {mutableStateOf("")}
    val imageDownload=androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.CreateDocument("application/octet-stream")) {uri->
        val spec=runCatching {JSONObject(downloadSpec)}.getOrNull()
        if(uri!=null&&spec!=null) scope.launch {try {
            val blob=onRequest("guest/workspace?scopeId=${android.net.Uri.encode(spec.getString("scopeId"))}&path=${android.net.Uri.encode(spec.getString("path"))}&blob=true",null)
            val content=android.util.Base64.decode(blob.getString("dataBase64"),android.util.Base64.DEFAULT)
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                context.contentResolver.openOutputStream(uri)?.use {it.write(content)} ?: error("Не удалось сохранить изображение")
            }
            error=""
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}
    }
    val attachmentPicker=androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.OpenDocument()) {uri->
        if(uri!=null) scope.launch {busy=true;try {
            val targetScope=currentScope;val targetConversation=conversation
            val content=kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                context.contentResolver.openInputStream(uri)?.use {stream->
                    val result=java.io.ByteArrayOutputStream();val buffer=ByteArray(65536)
                    while(result.size()<=4*1024*1024) {
                        val read=stream.read(buffer,0,minOf(buffer.size,4*1024*1024+1-result.size()))
                        if(read<0)break;result.write(buffer,0,read)
                    }
                    result.toByteArray()
                } ?: error("Файл недоступен")
            }
            require(content.size<=4*1024*1024){"Загрузка ограничена 4 МиБ"}
            var name="file"
            context.contentResolver.query(uri,arrayOf(android.provider.OpenableColumns.DISPLAY_NAME),null,null,null)?.use {if(it.moveToFirst())name=it.getString(0)}
            require(name.isNotBlank()&&!name.contains('/')&&!name.contains('\\')){"Некорректное имя файла"}
            val path="attachment-"+UUID.randomUUID().toString()+"-"+name
            onRequest("guest/upload",JSONObject().put("scopeId",targetScope).put("path",path).put("dataBase64",android.util.Base64.encodeToString(content,android.util.Base64.NO_WRAP)))
            val link="[Файл: ${name.replace(']', '_')}](/workspace/${android.net.Uri.encode(path,"/")})"
            if(targetScope==currentScope&&targetConversation==conversation) {draft=listOf(draft,link).filter {it.isNotBlank()}.joinToString("\n");persist()}
            else {
                val store=JSONObject(draftStore);val drafts=store.getJSONObject("drafts");val key="$targetScope/$targetConversation"
                drafts.put(key,listOf(drafts.optString(key),link).filter {it.isNotBlank()}.joinToString("\n"));draftStore=store.toString();onPersist(draftStore,pending)
            }
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}
    }
    if(files) {GuestWorkspaceScreen(currentScope,{files=false},initialPath=filePath,onRequest=onRequest);return}
    if(shared) {GuestSharedScreen({shared=false},{scopeId->selectChat(scopeId,"main");shared=false},onRequest);return}
    LaunchedEffect(Unit) {try {
        val list=onRequest("guest/models",null).getJSONArray("models")
        models=(0 until list.length()).map {list.getJSONObject(it)}
    }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;}}
    LaunchedEffect(Unit) {while(isActive){try{refresh();error=""}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()};delay(2000)}}
    val settings=context.getSharedPreferences("companion",android.content.Context.MODE_PRIVATE)
    var themeMode by remember {mutableStateOf(settings.getString("themeMode","system").orEmpty())}
    var updatesOpen by rememberSaveable {mutableStateOf(false)}
    val updates=remember {AppUpdates(context.applicationContext,scope)}
    if(updatesOpen) {UpdatesScreen(updates,{updatesOpen=false});return}
    fun submit() {
        if(busy||draft.isBlank()) return
        if(pending.isBlank()) {
            pending=JSONObject().put("operationId",UUID.randomUUID().toString()).put("text",draft).put("conversationId",conversation).put("scopeId",currentScope)
                .also {if(model.isNotBlank()) it.put("model",model);if(effort.isNotBlank()) it.put("effort",effort)}.toString()
            persist()
        }
        busy=true;scope.launch {try {
            onRequest("guest/send",JSONObject(pending));pending="";draft="";persist();refresh();error=""
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}
    }
    fun openFile(raw:String):String? {
        val path=android.net.Uri.decode(raw).removePrefix("file://").replace(Regex(":\\d+(?::\\d+)?$"),"")
        val relative=if(path.startsWith("/workspace/")) path.removePrefix("/workspace/") else path
        if(relative.startsWith("/")||relative.split('/').contains("..")){error="Ссылка находится вне гостевой рабочей папки";return null}
        filePath=relative;files=true;return relative
    }
    val rows=tasks.filter {it.optString("scopeId","default")==currentScope&&it.optString("conversationId","main")==conversation}
    val queued=rows.filter {it.optString("state") in listOf("queued","dispatching")}.map {
        LocalMessage(it.getString("operationId"),it.optString("text"),emptyList(),threadId="$currentScope/$conversation",acceptedByBridge=true)
    }.toMutableList()
    if(pending.isNotBlank()&&!tasks.any {it.optString("operationId")==JSONObject(pending).optString("operationId")}) {
        val p=JSONObject(pending)
        queued+=LocalMessage(p.getString("operationId"),p.optString("text"),emptyList(),threadId=conversation)
    }
    fun stopLabel(reason:String)=when(reason) {
        "storage_limit"->"Гостевое хранилище заполнено"
        "quota_limit"->"Выделенная квота исчерпана"
        "execution_disabled"->"Владелец выключил гостевой исполнитель"
        "sharing_revoked"->"Владелец отозвал доступ к ресурсу"
        "meter_uncertain"->"Расход требует проверки владельцем"
        "cancelled"->"Задача отменена"
        else->""
    }
    val taskLines=rows.filter {it.optString("state") !in listOf("queued","dispatching")}.flatMap {task->
        val id=task.getString("operationId");val state=task.optString("state");val result=task.optJSONObject("result")
        val output=result?.optJSONObject("output");val messages=output?.optJSONArray("messages")
        buildList {
            add(ChatLine("user",task.optString("text"),turnId=id,id="$id:user",time=task.optLong("created").toString()))
            if(state in listOf("running","cancel_requested")) add(ChatLine("process",guestTaskLabel(state),turnId=id,id="$id:process"))
            val artifacts=parseFileArtifacts(output?.optJSONArray("artifacts"))
            if(artifacts.isNotEmpty()) add(ChatLine("artifacts","Изменения файлов",turnId=id,id="$id:artifacts",artifacts=artifacts))
            if(messages!=null) for(i in 0 until messages.length()) {
                val message=messages.optString(i)
                val imagePattern=Regex("!\\[([^\\]]*)\\]\\(([^\\s)]+)\\)")
                val images=imagePattern.findAll(message).mapNotNull {match->
                    val raw=android.net.Uri.decode(match.groupValues[2]).removePrefix("file://")
                    val path=raw.removePrefix("/workspace/")
                    if(path.startsWith('/')||path.contains("://")||path.split('/').contains("..")) null
                    else ChatImage(path,match.groupValues[1].ifBlank {path.substringAfterLast('/')})
                }.toList()
                add(ChatLine("assistant",if(images.isNotEmpty()) imagePattern.replace(message,"") else message,turnId=id,id="$id:answer:$i",images=images))
            }
            if(state !in listOf("running","cancel_requested")) add(ChatLine("outcome",guestTaskLabel(state)+"\n"+stopLabel(output?.optString("stopReason").orEmpty()),turnId=id,id="$id:outcome",outcomeSummary=guestTaskLabel(state),
                quotaSummary=result?.optJSONObject("measurement")?.optJSONObject("usage")?.let {u->"${u.optDouble("fiveHours")} п.п. за 5ч · ${u.optDouble("week")} п.п. недели"}.orEmpty()))
        }
    }
    val stored=JSONObject(draftStore).getJSONObject("drafts").keys().asSequence().toList()
    val scopes=(tasks.map {it.optString("scopeId","default")}+stored.map {it.substringBefore('/')}+currentScope).distinct()
    val projects=scopes.map {sid->
        val scopeTasks=tasks.filter {it.optString("scopeId","default")==sid}
        val ids=(scopeTasks.map {it.optString("conversationId","main")}+stored.filter {it.startsWith("$sid/")}.map {it.substringAfter('/')}+if(sid==currentScope) listOf(conversation) else emptyList()).distinct()
        ProjectGroup(sid,if(sid=="default") "Мой проект" else "Рабочая копия ${sid.take(8)}",sid,ids.map {cid->
            val first=scopeTasks.firstOrNull {it.optString("conversationId","main")==cid}
            ThreadItem("$sid/$cid",first?.optString("text")?.take(60) ?: if(cid=="main") "Основной чат" else "Черновик ${cid.take(8)}","",first?.optLong("created") ?: 0)
        })
    }
    val catalog=models.map {m->ModelOption(m.getString("id"),m.optString("name",m.getString("id")),m.optJSONArray("efforts")?.let {a->(0 until a.length()).map {a.optString(it)}} ?: emptyList(),m.optString("defaultEffort"))}
    val runtime=guest?.optJSONObject("runtime")
    val active=rows.lastOrNull {it.optString("state") in listOf("running","cancel_requested")}
    val summary=listOf("fiveHours" to "5 ч","week" to "Неделя").joinToString(" · ") {(key,label)->
        val q=guest?.optJSONObject("quotas")?.optJSONObject(key)
        if(q?.optJSONObject("rule")?.optString("mode")=="unlimited") "$label: без ограничения" else "$label: ${q?.optDouble("remaining") ?: 0.0} п.п."
    }
    val waiting=if(queued.isNotEmpty()) {
        if(tasks.any {it.optString("state")=="uncertain"}) "Владелец должен согласовать расход прерванной задачи, чтобы освободить очередь"
        else runtime?.optString("reason").orEmpty()
    } else ""
    val chatLines=listOf(ChatLine("system",summary,id="guest-quota")) +
        if(waiting.isNotBlank()) listOf(ChatLine("system",waiting,id="guest-wait"))+taskLines else taskLines
    val dark=when(themeMode){"dark"->true;"light"->false;else->androidx.compose.foundation.isSystemInDarkTheme()}
    val imageScope=LocalImageScope.current+":"+currentScope+":"+conversation+":"+(rows.lastOrNull()?.optString("operationId").orEmpty())+":"+(rows.lastOrNull()?.optString("state").orEmpty())
    CompositionLocalProvider(LocalImageScope provides imageScope) {
    MaterialTheme(colorScheme=if(dark) darkPalette else lightPalette,typography=appTypography) {
        CompanionUi(
            onUpdates = {updatesOpen=true},
            updateAvailable = false,
            paired = true,
            relayOnly = true,
            themeMode = themeMode,
            onThemeMode = {themeMode=it;settings.edit().putString("themeMode",it).apply();onThemeChanged()},
            title = projects.flatMap {it.threads}.find {it.id=="$currentScope/$conversation"}?.title ?: name,
            projectName = "Мой проект",
            status = if(active!=null) "Codex работает" else if(queued.isNotEmpty()) "В очереди" else if(error.isNotBlank()) "Ошибка подключения" else "Подключено",
            connectionState = if(error.isBlank()) ConnectionState.Online else ConnectionState.Reconnecting,
            usageLimits = null,
            limitsLoading = false,
            limitsError = summary,
            lines = chatLines,
            queue = queued,
            selectedThreadId = "$currentScope/$conversation",
            projects = projects,
            catalogLoading = false,
            catalogError = "",
            models = catalog,
            selectedModel = model,
            selectedEffort = effort,
            modelOverridden = model.isNotBlank(),
            effortOverridden = effort.isNotBlank(),
            input = draft,
            attachments = emptyList(),
            outboxFiles = emptyList(),
            outboxLoading = false,
            outboxError = "",
            workspaceRoot = "Мой проект",
            workspacePath = filePath,
            workspaceEntries = emptyList(),
            workspaceLoading = false,
            workspaceError = "",
            workspaceTruncated = false,
            previewPath = "",
            previewText = "",
            previewNote = "",
            previewLoading = false,
            fileStatus = "",
            transferringFileId = "",
            transferProgress = null,
            historyHasMore = nextCursor.isNotBlank(),
            historyLoading = false,
            historyError = error,
            initialHistoryLoading = guest==null&&error.isBlank(),
            initialHistoryError = if(guest==null) error else "",
            onRetryHistory = {scope.launch {try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}},
            onInput = {draft=it;persist()},
            onScan = {},
            onSelectThread = {selectChat(it.substringBefore('/'),it.substringAfter('/'))},
            onDeleteThread = {it -> error("Действие недоступно гостевому подключению")},
            onMoveThread = {_, _ -> error("Действие недоступно гостевому подключению")},
            onNewChat = {sid->selectChat(sid ?: currentScope,UUID.randomUUID().toString())},
            onRefreshCatalog = {scope.launch {try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}},
            onRefreshLimits = {scope.launch {try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}},
            onResetLimits = {it -> error("Действие недоступно гостевому подключению")},
            resetMessage = "",
            resetLoading = false,
            resetPending = false,
            onSubagentRequest = {_, _ -> error("Действие недоступно гостевому подключению")},
            onAdbRequest = {_, _ -> error("Действие недоступно гостевому подключению")},
            onModel = {model=it},
            onEffort = {effort=it},
            onAttach = {if(!busy&&pending.isBlank()) attachmentPicker.launch(arrayOf("*/*"))},
            onRemoveAttachment = {it -> error("Действие недоступно гостевому подключению")},
            onFetchFiles = {filePath="";files=true},
            onSaveFile = {it -> error("Действие недоступно гостевому подключению")},
            onBrowseWorkspace = {filePath=it;files=true},
            onPreviewWorkspace = {openFile(it)},
            onAskWorkspace = {it -> error("Действие недоступно гостевому подключению")},
            onSaveWorkspace = {it -> error("Действие недоступно гостевому подключению")},
            onResolveProjectFile = {openFile(it)},
            onSaveChatImage = {image->downloadSpec=JSONObject().put("scopeId",currentScope).put("path",image.id).toString();imageDownload.launch(image.name)},
            onLoadOlder = {scope.launch {try{
 val response=onRequest("guest/tasks?before=${android.net.Uri.encode(nextCursor)}",null);val a=response.getJSONArray("tasks")
 tasks=((0 until a.length()).map {a.getJSONObject(it)}.reversed()+tasks).distinctBy {it.getString("operationId")}
 nextCursor=response.optString("nextCursor").takeUnless {it=="null"}.orEmpty();olderLoaded=true
 }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}},
            onSend = {submit()},
            onCancelQueued = {item->busy=true;scope.launch {try{onRequest("guest/cancel",JSONObject().put("operationId",item.id));refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}},
            onSteerQueued = {it -> error("Действие недоступно гостевому подключению")},
            onCancelTransfer = {},
            onDisconnect = onClose,
            loadImage = {path->val file=onRequest("guest/workspace?scopeId=${android.net.Uri.encode(currentScope)}&path=${android.net.Uri.encode(path)}&blob=true",null);android.util.Base64.decode(file.getString("dataBase64"),android.util.Base64.DEFAULT)},
            guestMode=true,inputEnabled=!busy&&pending.isBlank(),sendEnabled=!busy&&(guest?.optBoolean("executionAvailable")==true||pending.isNotBlank()),
            onGuestFiles={filePath="";files=true},onGuests={shared=true},
            onGuestCancelActive={active?.let {task->scope.launch {try{onRequest("guest/cancel",JSONObject().put("operationId",task.getString("operationId")));refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}}}},
            activeTurnId=active?.optString("operationId").orEmpty(),
            compatibilityNote=listOf(summary,error,runtime?.optString("reason").orEmpty()).filter {it.isNotBlank()}.joinToString("\n"),
        )
    }
    }
}
