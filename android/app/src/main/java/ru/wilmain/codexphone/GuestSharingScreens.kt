package ru.wilmain.codexphone

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID

@Composable
internal fun OwnerGuestSharingScreen(guestId:String,onClose:()->Unit,onRequest:suspend(String,JSONObject?)->JSONObject) {
    BackHandler(onBack=onClose)
    val scope=rememberCoroutineScope()
    val clipboard=androidx.compose.ui.platform.LocalClipboardManager.current
    var projects by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var guests by remember {mutableStateOf<JSONObject?>(null)}
    var error by remember {mutableStateOf("")}
    var selected by remember {mutableStateOf<JSONObject?>(null)}
    var busy by remember {mutableStateOf(false)}
    var copyCursor by remember {mutableStateOf("")}
    var legacyCopies by remember {mutableStateOf(false)}
    var reviews by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var reviewRows by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var copyId by remember {mutableStateOf("")}
    var preview by remember {mutableStateOf<JSONObject?>(null)}
    var applyConfirmation by remember {mutableStateOf<JSONObject?>(null)}
    suspend fun refresh() {
        val ps=onRequest("projects",null).optJSONArray("projects")
        projects=if(ps==null) emptyList() else (0 until ps.length()).map {ps.getJSONObject(it)}
        val response=onRequest("guests",null)
        val gs=response.getJSONArray("guests")
        guests=(0 until gs.length()).map {gs.getJSONObject(it)}.find {it.optString("id")==guestId}
        legacyCopies=!response.optBoolean("copiesListingAvailable")
        if(legacyCopies) {
            val ts=onRequest("guests/tasks",null).getJSONArray("tasks")
            reviews=(0 until ts.length()).map {ts.getJSONObject(it)}.filter {it.optString("guestId")==guestId&&it.optString("scopeId","default")!="default"}.distinctBy {it.optString("scopeId")}
            copyCursor=""
        } else {
            val page=onRequest("guests/copies?guestId=${android.net.Uri.encode(guestId)}",null)
            val rows=page.getJSONArray("copies")
            reviews=(0 until rows.length()).map {rows.getJSONObject(it).also {r->r.put("scopeId",r.getString("id"))}}
            copyCursor=page.optString("nextCursor").takeUnless {it=="null"}.orEmpty()
        }
    }
    LaunchedEffect(guestId){try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}
    selected?.let {resource->AlertDialog(onDismissRequest={selected=null},title={Text("Предоставить доступ?")},text={Text("Гость сможет читать содержимое и историю выбранного ресурса. Проверьте, что там нет секретов. Работа выполняется в отдельной копии; изменения принимаете вы.")},
        confirmButton={Column {
            for((right,label) in listOf("view" to "Только просмотр","work" to "Просмотр и рабочая копия")) TextButton(enabled=!busy,onClick={busy=true;scope.launch {try{
                onRequest("guests/action",JSONObject(resource.toString()).put("guestId",guestId).put("action","grant").put("right",right).put("confirmed",true).put("operationId",UUID.randomUUID().toString()));selected=null;refresh()
            }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text(label)}
        }},dismissButton={TextButton(onClick={selected=null}){Text("Отмена")}})}
    applyConfirmation?.let {p->AlertDialog(
        onDismissRequest={if(!busy)applyConfirmation=null},
        title={Text("Применить изменение?")},
        text={Text("Файл ${p.getString("path")} на вашем ПК будет заменён показанной версией из копии ${p.getString("scopeId").take(8)}. Остальные файлы не изменятся.")},
        dismissButton={TextButton(enabled=!busy,onClick={applyConfirmation=null}){Text("Отмена")}},
        confirmButton={TextButton(enabled=!busy,onClick={busy=true;scope.launch{try{
            val requestedCopy=p.getString("scopeId")
            onRequest("guests/review",JSONObject().put("scopeId",requestedCopy).put("path",p.getString("path")).put("guestHash",p.getString("guestHash")).put("confirmed",true))
            applyConfirmation=null;preview=null;error=""
            val rows=onRequest("guests/review",JSONObject().put("scopeId",requestedCopy)).getJSONArray("changes")
            if(copyId==requestedCopy)reviewRows=(0 until rows.length()).map {rows.getJSONObject(it)}
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Применить")}}
    )}

    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        UiScreenHeader("Общий доступ",onClose)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
            TextButton(enabled=!busy,onClick={busy=true;scope.launch{try{refresh();error=""}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Обновить список")}
            Text("Переданные ресурсы",style=MaterialTheme.typography.titleLarge)
            val grants=guests?.optJSONArray("grants")
            if(grants!=null) for(i in 0 until grants.length()) {
                val g=grants.getJSONObject(i)
                Text("${g.optString("kind")}: ${g.optString("resourceId")} · ${if(g.optString("right")=="work") "Работа в копии" else "Просмотр"}")
                TextButton(enabled=!busy,onClick={busy=true;scope.launch{try{
                    onRequest("guests/action",JSONObject(g.toString()).put("guestId",guestId).put("action","unshare").put("operationId",UUID.randomUUID().toString()));refresh()
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Отозвать этот доступ")}
            }
            Text("Проекты и чаты",style=MaterialTheme.typography.titleLarge)
            projects.forEach {p->
                Text(p.optString("name"),style=MaterialTheme.typography.titleMedium)
                if(p.optString("id")!="other"&&p.optString("cwd").isNotBlank()) TextButton(enabled=!busy,onClick={selected=JSONObject().put("kind","project").put("resourceId",p.getString("id"))}){Text("Поделиться проектом")}
                val chats=p.optJSONArray("threads")
                if(chats!=null) for(i in 0 until chats.length()) {
                    val t=chats.getJSONObject(i)
                    TextButton(enabled=!busy,onClick={selected=JSONObject().put("kind","thread").put("resourceId",t.getString("id"))}){Text(t.optString("title","Чат"))}
                }
            }
            Text("Изменения гостя",style=MaterialTheme.typography.titleLarge)
            if(legacyCopies)Text("Обновите агент для просмотра всех рабочих копий, включая копии без запущенных задач.",style=MaterialTheme.typography.bodySmall)
            if(copyCursor.isNotBlank())TextButton(enabled=!busy,onClick={
                val cursor=copyCursor;busy=true;scope.launch{try{
                    val page=onRequest("guests/copies?guestId=${android.net.Uri.encode(guestId)}&before=${android.net.Uri.encode(cursor)}",null)
                    val rows=page.getJSONArray("copies")
                    reviews=(reviews+(0 until rows.length()).map {rows.getJSONObject(it).also {r->r.put("scopeId",r.getString("id"))}}).distinctBy {it.getString("scopeId")}
                    copyCursor=page.optString("nextCursor").takeUnless {it=="null"}.orEmpty()
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}
            }){Text("Показать ещё копии")}
            reviews.forEach {task->TextButton(enabled=!busy,onClick={
                val requestedCopy=task.getString("scopeId")
                copyId=requestedCopy;reviewRows=emptyList();preview=null;busy=true
                scope.launch{try{
                    val rows=onRequest("guests/review",JSONObject().put("scopeId",requestedCopy)).getJSONArray("changes")
                    if(copyId==requestedCopy)reviewRows=(0 until rows.length()).map {rows.getJSONObject(it)}
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}
            }){Text("Просмотреть копию "+task.getString("scopeId").take(8))}}
            reviewRows.forEach {row->
                Text(row.optString("path")+(if(row.optBoolean("conflict")) " · файл владельца изменился" else ""))
                TextButton(enabled=!busy&&!row.optBoolean("blocked"),onClick={
                    val requestedCopy=copyId;val requestedPath=row.getString("path")
                    preview=null;busy=true
                    scope.launch{try{
                        val result=onRequest("guests/review",JSONObject().put("scopeId",requestedCopy).put("path",requestedPath))
                        if(copyId==requestedCopy)preview=result.put("path",requestedPath).put("scopeId",requestedCopy)
                    }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}finally{busy=false}}
                }){Text("Посмотреть diff")}
            }
            preview?.let {p->
                Text(p.getString("path"),style=MaterialTheme.typography.titleMedium)
                ArtifactDiff(p.optString("diff"))
                TextButton(onClick={clipboard.setText(androidx.compose.ui.text.AnnotatedString(p.optString("diff")))}){Text("Копировать diff")}
                TextButton(enabled=!busy&&!p.isNull("guestHash"),onClick={applyConfirmation=JSONObject(p.toString())}){Text("Принять изменение этого файла")}
                Text("Перед применением вы подтвердите замену выбранного файла. Удаление файлов здесь не выполняется.",style=MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
internal fun GuestSharedScreen(onClose:()->Unit,onWork:(String)->Unit,onRequest:suspend(String,JSONObject?)->JSONObject) {
    val scope=rememberCoroutineScope()
    var resources by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var resourceJson by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    val resource=resourceJson.takeIf {it.isNotBlank()}?.let {JSONObject(it)}
    var viewer by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf(false)}
    var viewerPath by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    var preview by remember {mutableStateOf("")}
    var error by remember {mutableStateOf("")}
    var loading by remember {mutableStateOf(false)}
    var historyLoading by remember {mutableStateOf(false)}
    var historyCursor by remember {mutableStateOf("")}
    var historyRows by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var historyThread by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    suspend fun loadHistory(older:Boolean=false) {
        if(historyLoading||historyThread.isBlank())return
        val requested=historyThread;val cursor=if(older)historyCursor else ""
        historyLoading=true
        try {
            val result=onRequest("guest/history?threadId=${android.net.Uri.encode(requested)}"+if(cursor.isBlank()) "" else "&before=${android.net.Uri.encode(cursor)}",null)
            val rows=result.getJSONArray("turns")
            if(historyThread==requested) {
                val page=(0 until rows.length()).map {rows.getJSONObject(it)}
                historyRows=if(older)page+historyRows else page
                preview=historyRows.joinToString("\n\n"){it.optString("text")}
                historyCursor=result.optString("nextBefore").takeUnless {it=="null"}.orEmpty();error=""
            }
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}
        finally{historyLoading=false}
    }
    suspend fun refreshResources() {
        if(loading)return
        loading=true
        try {
            val rows=onRequest("guest/shared",null).getJSONArray("resources")
            resources=(0 until rows.length()).map {rows.getJSONObject(it)};error=""
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException)throw e;error=e.message.orEmpty()}
        finally{loading=false}
    }
    fun closeHistoryOrScreen() {if(historyThread.isNotBlank())historyThread="" else onClose()}
    BackHandler(onBack={closeHistoryOrScreen()})
    LaunchedEffect(historyThread) {preview="";historyRows=emptyList();historyCursor="";historyLoading=false;loadHistory()}
    LaunchedEffect(Unit){refreshResources()}
    if(viewer&&resource!=null) {
        val selected=resource
        GuestWorkspaceScreen("shared-${selected.optString("kind")}-${selected.getString("resourceId")}",{viewer=false},initialPath=viewerPath,readOnly=true,title="Общие файлы") {url,body->
            require(body==null) {"Изменять оригинал нельзя. Создайте рабочую копию."}
            val query=android.net.Uri.parse("https://fixture/$url")
            val path=query.getQueryParameter("path").orEmpty()
            val blob=query.getQueryParameter("blob")=="true"
            onRequest("guest/resource?kind=${selected.getString("kind")}&resourceId=${android.net.Uri.encode(selected.getString("resourceId"))}&path=${android.net.Uri.encode(path)}&blob=$blob",null)
        }
        return
    }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        UiScreenHeader(if(historyThread.isBlank()) "Общие ресурсы" else "История общего чата",{closeHistoryOrScreen()})
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
            if(loading||historyLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
            TextButton(enabled=!loading&&!historyLoading,onClick={scope.launch{if(historyThread.isNotBlank())loadHistory() else refreshResources()}}){Text("Обновить") }
            if(resources.isEmpty()&&!loading&&error.isBlank()) Text("Владелец пока не предоставил проекты или чаты")
            if(historyThread.isBlank()) resources.forEach {r->
                Text(r.optString("name",r.optString("resourceId")),style=MaterialTheme.typography.titleMedium)
                TextButton(onClick={resourceJson=r.toString();historyThread="";viewerPath="";viewer=true}){Text("Просмотреть файлы")}
                if(r.optString("kind")=="thread") TextButton(onClick={resourceJson=r.toString();historyThread=r.getString("resourceId")}){Text("Просмотреть историю чата")}
                r.optJSONArray("threads")?.let {threads->for(i in 0 until threads.length()) {
                    val thread=threads.getJSONObject(i)
                    TextButton(onClick={resourceJson=r.toString();historyThread=thread.getString("id")}){Text(thread.optString("name",thread.getString("id")))}
                }}
                if(r.optString("right")=="work") TextButton(onClick={scope.launch{try{
                    val copy=onRequest("guest/copy",JSONObject(r.toString()).put("operationId","copy-"+java.security.MessageDigest.getInstance("SHA-256").digest((r.optString("kind")+r.getString("resourceId")).toByteArray()).joinToString(""){"%02x".format(it)}))
                    onWork(copy.getString("scopeId"))
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}}){Text("Работать в моей копии")}
            }
            if(historyThread.isNotBlank()&&!historyLoading&&historyRows.isEmpty()&&error.isBlank()) Text("В этом чате пока нет сообщений")
            if(historyThread.isNotBlank()) Text("История доступна только для просмотра",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            if(historyCursor.isNotBlank()) TextButton(enabled=!historyLoading,onClick={scope.launch{loadHistory(true)}}){Text("Показать ранние сообщения")}
            if(preview.isNotBlank()) SelectionContainer {MarkdownContent(preview,onProjectFile={raw->
                viewerPath=android.net.Uri.decode(raw).removePrefix("file://").replace(Regex(":\\d+(?::\\d+)?$"),"")
                if(resource!=null) viewer=true else error="Сначала выберите предоставленный ресурс"
            })}
        }
    }
}
