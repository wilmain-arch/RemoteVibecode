package ru.wilmain.codexphone

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

@Composable
internal fun GuestWorkspaceScreen(scopeId:String,onClose:()->Unit,initialPath:String="",readOnly:Boolean=false,title:String="Мои файлы",onRequest:suspend(String,JSONObject?)->JSONObject) {
    BackHandler(onBack=onClose)
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var directory by rememberSaveable(scopeId) {mutableStateOf(initialPath.substringBeforeLast('/',""))}
    var selectedPath by rememberSaveable(scopeId) {mutableStateOf(initialPath)}
    var previewRevision by remember {mutableStateOf(0)}
    var uploadDirectory by rememberSaveable {mutableStateOf("")}
    var savePath by rememberSaveable {mutableStateOf("")}
    var state by remember {mutableStateOf<JSONObject?>(null)}
    var blob by remember {mutableStateOf<JSONObject?>(null)}
    var bytes by remember {mutableStateOf<ByteArray?>(null)}
    var error by remember {mutableStateOf("")}
    var busy by remember {mutableStateOf(false)}
    var listingLoading by remember {mutableStateOf(false)}
    var listingError by remember {mutableStateOf("")}
    var previewError by remember {mutableStateOf("")}
    var listingRevision by remember {mutableStateOf(0)}
    suspend fun refresh() {
        val requested=directory;val revision=++listingRevision
        listingLoading=true
        try {
            val result=onRequest("guest/workspace?scopeId=$scopeId&path=${android.net.Uri.encode(requested)}",null)
            if(directory==requested&&revision==listingRevision) {state=result;listingError=""}
        }catch(e:Exception){
            if(e is kotlinx.coroutines.CancellationException)throw e
            if(directory==requested&&revision==listingRevision)listingError=e.message.orEmpty()
        }finally{if(revision==listingRevision)listingLoading=false}
    }
    val save=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) {uri->
        if(uri!=null) scope.launch {try{withContext(Dispatchers.IO){val result=onRequest("guest/workspace?scopeId=$scopeId&path=${android.net.Uri.encode(savePath)}&blob=true",null);val content=android.util.Base64.decode(result.getString("dataBase64"),android.util.Base64.DEFAULT);context.contentResolver.openOutputStream(uri)?.use {it.write(content)} ?: error("Не удалось сохранить файл")}}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}}
    }
    val upload=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {uri->
        if(uri!=null) scope.launch {busy=true;try{
            val data=withContext(Dispatchers.IO){context.contentResolver.openInputStream(uri)?.use {val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(65536)
                while(output.size()<=4*1024*1024) {val read=it.read(buffer,0,minOf(buffer.size,4*1024*1024+1-output.size()));if(read<0) break;output.write(buffer,0,read)}
                output.toByteArray()} ?: error("Файл недоступен")}
            require(data.size<=4*1024*1024){"Загрузка ограничена 4 МиБ"}
            var name="file"
            context.contentResolver.query(uri,arrayOf(android.provider.OpenableColumns.DISPLAY_NAME),null,null,null)?.use {cursor->if(cursor.moveToFirst()) name=cursor.getString(0)}
            require(name.isNotBlank()&&!name.contains('/')&&!name.contains('\\')){"Некорректное имя файла"}
            onRequest("guest/upload",JSONObject().put("scopeId",scopeId).put("path",listOf(uploadDirectory,name).filter {it.isNotBlank()}.joinToString("/"))
                .put("dataBase64",android.util.Base64.encodeToString(data,android.util.Base64.NO_WRAP)));refresh()
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}
    }
    LaunchedEffect(scopeId,directory){state=null;refresh()}
    LaunchedEffect(scopeId,selectedPath,previewRevision) {
        blob=null;bytes=null;previewError=""
        if(selectedPath.isNotBlank()) {busy=true;try {
            val requested=selectedPath
            val result=onRequest("guest/workspace?scopeId=$scopeId&path=${android.net.Uri.encode(requested)}&blob=true",null)
            if(selectedPath==requested) {blob=result;bytes=android.util.Base64.decode(result.getString("dataBase64"),android.util.Base64.DEFAULT);previewError=""}
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;previewError=e.message.orEmpty()}finally{busy=false}}
    }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        UiScreenHeader(title,onClose)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            Text(directory.ifBlank {"Рабочая папка"},style=MaterialTheme.typography.titleLarge)
            val shownError=listOf(error,listingError,previewError).filter {it.isNotBlank()}.distinct().joinToString("\n")
            if(shownError.isNotBlank()) Text(shownError,color=MaterialTheme.colorScheme.error)
            if(listingError.isNotBlank())TextButton(enabled=!listingLoading,onClick={scope.launch{refresh()}}){Text("Повторить список")}
            if(busy||listingLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
            if(!readOnly) TextButton(enabled=!busy&&!listingLoading,onClick={uploadDirectory=directory;upload.launch(arrayOf("*/*"))}){Text("Загрузить файл · до 4 МиБ")}
            if(previewError.isNotBlank()&&selectedPath.isNotBlank()) TextButton(enabled=!busy,onClick={previewRevision++}){Text("Повторить открытие файла")}
            if(state?.optJSONArray("entries")?.length()==0) Text("В этой папке пока нет файлов",color=MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(enabled=!busy,onClick={selectedPath="";directory=directory.substringBeforeLast('/',"")}){Text("Вверх")}
            val entries=state?.optJSONArray("entries")
            if(entries!=null) for(i in 0 until entries.length()) {
                val file=entries.getJSONObject(i)
                TextButton(enabled=!busy,onClick={if(file.optBoolean("isDirectory")) {selectedPath="";directory=file.getString("path")} else selectedPath=file.getString("path")}){Text(file.getString("name"))}
            }
            blob?.let {file->
                Text(file.optString("name"),style=MaterialTheme.typography.titleMedium)
                bytes?.let {content->
                    if(file.optString("mime").startsWith("image/")) {
                        val decoded by produceState<Pair<Boolean,android.graphics.Bitmap?>>(true to null,content) {
                            value=true to null
                            value=false to withContext(Dispatchers.Default) {
                                val options=android.graphics.BitmapFactory.Options().also {it.inJustDecodeBounds=true}
                                android.graphics.BitmapFactory.decodeByteArray(content,0,content.size,options)
                                options.inJustDecodeBounds=false;options.inSampleSize=1
                                while(maxOf(options.outWidth,options.outHeight)/options.inSampleSize>2048)options.inSampleSize*=2
                                android.graphics.BitmapFactory.decodeByteArray(content,0,content.size,options)
                            }
                        }
                        val bitmap=decoded.second
                        if(decoded.first)LinearProgressIndicator(Modifier.fillMaxWidth())
                        else if(bitmap==null)Text("Не удалось прочитать изображение. Скачайте файл для открытия.",color=MaterialTheme.colorScheme.error)
                        bitmap?.let {
                            var scale by remember(content) {mutableStateOf(1f)}
                            var offset by remember(content) {mutableStateOf(Offset.Zero)}
                            val transform=rememberTransformableState {zoom,pan,_->scale=(scale*zoom).coerceIn(1f,5f);offset=if(scale==1f) Offset.Zero else offset+pan}
                            Box(Modifier.fillMaxWidth().height(320.dp).clipToBounds()) {
                                Image(it.asImageBitmap(),contentDescription=file.optString("name")+". Масштабирование двумя пальцами",
                                    modifier=Modifier.fillMaxSize().graphicsLayer {scaleX=scale;scaleY=scale;translationX=offset.x;translationY=offset.y}.transformable(transform))
                            }
                            TextButton(onClick={scale=1f;offset=Offset.Zero}){Text("Сбросить масштаб")}
                        }
                    } else if(!content.contains(0.toByte())) {
                        val text=content.toString(Charsets.UTF_8)
                        SelectionContainer {Text(text.take(50000))}
                        if(text.length>50000)Text("Показаны первые 50000 символов. Скачайте файл для полного просмотра.",style=MaterialTheme.typography.bodySmall)
                    } else Text("Двоичный файл. Скачайте его для открытия.",style=MaterialTheme.typography.bodyMedium)
                }
                TextButton(onClick={savePath=selectedPath;save.launch(file.optString("name","file"))}){Text("Скачать файл")}
            }
        }
    }
}
