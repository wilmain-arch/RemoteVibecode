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
    var projects by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var guests by remember {mutableStateOf<JSONObject?>(null)}
    var error by remember {mutableStateOf("")}
    var selected by remember {mutableStateOf<JSONObject?>(null)}
    var busy by remember {mutableStateOf(false)}
    var reviews by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var reviewRows by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var copyId by remember {mutableStateOf("")}
    var preview by remember {mutableStateOf<JSONObject?>(null)}
    suspend fun refresh() {
        val ps=onRequest("projects",null).optJSONArray("projects")
        projects=if(ps==null) emptyList() else (0 until ps.length()).map {ps.getJSONObject(it)}
        val gs=onRequest("guests",null).getJSONArray("guests")
        guests=(0 until gs.length()).map {gs.getJSONObject(it)}.find {it.optString("id")==guestId}
        val ts=onRequest("guests/tasks",null).getJSONArray("tasks")
        reviews=(0 until ts.length()).map {ts.getJSONObject(it)}.filter {it.optString("guestId")==guestId&&it.optString("scopeId","default")!="default"}.distinctBy {it.optString("scopeId")}
    }
    LaunchedEffect(guestId){try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}
    selected?.let {resource->AlertDialog(onDismissRequest={selected=null},title={Text("Предоставить доступ?")},text={Text("Гость сможет читать содержимое и историю выбранного ресурса. Проверьте, что там нет секретов. Работа выполняется в отдельной копии; изменения принимаете вы.")},
        confirmButton={Column {
            for((right,label) in listOf("view" to "Только просмотр","work" to "Просмотр и рабочая копия")) TextButton(enabled=!busy,onClick={busy=true;scope.launch {try{
                onRequest("guests/action",JSONObject(resource.toString()).put("guestId",guestId).put("action","grant").put("right",right).put("confirmed",true).put("operationId",UUID.randomUUID().toString()));selected=null;refresh()
            }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text(label)}
        }},dismissButton={TextButton(onClick={selected=null}){Text("Отмена")}})}
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        UiScreenHeader("Общий доступ",onClose)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
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
                if(p.optString("id")!="other"&&p.optString("cwd").isNotBlank()) TextButton(onClick={selected=JSONObject().put("kind","project").put("resourceId",p.getString("id"))}){Text("Поделиться проектом")}
                val chats=p.optJSONArray("threads")
                if(chats!=null) for(i in 0 until chats.length()) {
                    val t=chats.getJSONObject(i)
                    TextButton(onClick={selected=JSONObject().put("kind","thread").put("resourceId",t.getString("id"))}){Text(t.optString("title","Чат"))}
                }
            }
            Text("Изменения гостя",style=MaterialTheme.typography.titleLarge)
            reviews.forEach {task->TextButton(onClick={copyId=task.getString("scopeId");scope.launch{try{
                val rows=onRequest("guests/review",JSONObject().put("scopeId",copyId)).getJSONArray("changes")
                reviewRows=(0 until rows.length()).map {rows.getJSONObject(it)}
            }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}}){Text("Просмотреть копию "+task.getString("scopeId").take(8))}}
            reviewRows.forEach {row->
                Text(row.optString("path")+(if(row.optBoolean("conflict")) " · файл владельца изменился" else ""))
                TextButton(onClick={scope.launch{try{
                    preview=onRequest("guests/review",JSONObject().put("scopeId",copyId).put("path",row.getString("path"))).put("path",row.getString("path"))
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}}){Text("Посмотреть diff")}
            }
            preview?.let {p->
                SelectionContainer {Text(p.optString("diff"),style=MaterialTheme.typography.bodySmall)}
                TextButton(onClick={selected=null;scope.launch {try{
                    onRequest("guests/review",JSONObject().put("scopeId",copyId).put("path",p.getString("path")).put("guestHash",p.optString("guestHash")).put("confirmed",true));preview=null
                    val rows=onRequest("guests/review",JSONObject().put("scopeId",copyId)).getJSONArray("changes")
                    reviewRows=(0 until rows.length()).map {rows.getJSONObject(it)}
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}}){Text("Принять изменение этого файла")}
                Text("Нажатие применяет показанное изменение к файлу на вашем ПК. Удаление файлов здесь не выполняется.",style=MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
internal fun GuestSharedScreen(onClose:()->Unit,onWork:(String)->Unit,onRequest:suspend(String,JSONObject?)->JSONObject) {
    BackHandler(onBack=onClose)
    val scope=rememberCoroutineScope()
    var resources by remember {mutableStateOf<List<JSONObject>>(emptyList())}
    var resourceJson by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    val resource=resourceJson.takeIf {it.isNotBlank()}?.let {JSONObject(it)}
    var viewer by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf(false)}
    var viewerPath by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    var preview by remember {mutableStateOf("")}
    var error by remember {mutableStateOf("")}
    var historyThread by androidx.compose.runtime.saveable.rememberSaveable {mutableStateOf("")}
    LaunchedEffect(historyThread) {preview="";if(historyThread.isNotBlank()) try {
        val rows=onRequest("guest/history?threadId=${android.net.Uri.encode(historyThread)}",null).getJSONArray("turns")
        preview=(0 until rows.length()).joinToString("\n\n"){rows.getJSONObject(it).optString("text")}
    }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}
    LaunchedEffect(Unit){try{
        val rows=onRequest("guest/shared",null).getJSONArray("resources")
        resources=(0 until rows.length()).map {rows.getJSONObject(it)}
    }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}
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
        UiScreenHeader("Общие ресурсы",onClose)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
            if(resources.isEmpty()) Text("Владелец пока не предоставил проекты или чаты")
            resources.forEach {r->
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
            if(preview.isNotBlank()) SelectionContainer {MarkdownContent(preview,onProjectFile={raw->
                viewerPath=android.net.Uri.decode(raw).removePrefix("file://").replace(Regex(":\\d+(?::\\d+)?$"),"")
                if(resource!=null) viewer=true else error="Сначала выберите предоставленный ресурс"
            })}
        }
    }
}
