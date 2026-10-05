package ru.wilmain.codexphone

import android.content.Intent
import android.net.Uri
import android.util.Base64
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.security.SecureRandom
import java.util.UUID

internal fun newGuestSecret(): String = ByteArray(32).also { SecureRandom().nextBytes(it) }.let {
    Base64.encodeToString(it, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
}

@Composable
private fun QuotaEditor(label: String, raw: String, enabled:Boolean=true, onChange: (String) -> Unit) {
    val rule=JSONObject(raw)
    Text(label, style=MaterialTheme.typography.titleMedium)
    listOf("unlimited" to "Без ограничения", "renewing" to "Пополнять после сброса", "fixed" to "Однократный бюджет").forEach { (mode,title) ->
        TextButton(enabled=enabled,onClick={onChange(rule.put("mode",mode).toString())},modifier=Modifier.heightIn(min=48.dp)) {
            Text(if(rule.optString("mode")==mode) "● $title" else title)
        }
    }
    if(rule.optString("mode")!="unlimited") {
        OutlinedTextField(enabled=enabled,keyboardOptions=androidx.compose.foundation.text.KeyboardOptions(keyboardType=androidx.compose.ui.text.input.KeyboardType.Decimal),value=rule.optString("amount","15"),onValueChange={onChange(rule.put("amount",it).toString())},label={Text("Размер, %")},singleLine=true,modifier=Modifier.fillMaxWidth())
        TextButton(enabled=enabled,onClick={onChange(rule.put("basis",if(rule.optString("basis")=="full") "remaining" else "full").toString())},modifier=Modifier.heightIn(min=48.dp)) {
            Text(if(rule.optString("basis")=="full") "От полной квоты" else "От остатка при выделении")
        }
    }
}

@Composable
internal fun GuestAccessScreen(host:String, fingerprint:String, onClose:()->Unit, onOpenGuest:(String)->Unit = {}, onRequest:suspend(String,JSONObject?)->JSONObject) {
    Surface(Modifier.fillMaxSize()) { GuestAccessContent(host,fingerprint,onClose,onOpenGuest,onRequest) }
}

@Composable
private fun GuestAccessContent(host:String, fingerprint:String, onClose:()->Unit, onOpenGuest:(String)->Unit, onRequest:suspend(String,JSONObject?)->JSONObject) {
    BackHandler(onBack=onClose)
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var state by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var form by rememberSaveable { mutableStateOf(false) }
    var name by rememberSaveable { mutableStateOf("") }
    var five by rememberSaveable { mutableStateOf("{\"mode\":\"unlimited\",\"basis\":\"full\",\"amount\":15}") }
    var week by rememberSaveable { mutableStateOf("{\"mode\":\"fixed\",\"basis\":\"full\",\"amount\":15}") }
    var pending by rememberSaveable { mutableStateOf("") }
    var link by rememberSaveable { mutableStateOf("") }
    var qr by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    LaunchedEffect(link) {
        qr=null
        if(link.isNotBlank()) try {
            val encoded=onRequest("guests/qr",JSONObject().put("link",link)).getString("pngBase64")
            val bytes=android.util.Base64.decode(encoded,android.util.Base64.DEFAULT)
            qr=android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size)
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;}
    }
    var editing by rememberSaveable { mutableStateOf("") }
    var revoke by rememberSaveable { mutableStateOf("") }
    var sharing by rememberSaveable { mutableStateOf("") }
    var reallocating by rememberSaveable { mutableStateOf(false) }
    var fiveSelected by rememberSaveable { mutableStateOf(true) }
    var weekSelected by rememberSaveable { mutableStateOf(true) }
    var resolution by remember { mutableStateOf<JSONObject?>(null) }
    var resolveFive by rememberSaveable { mutableStateOf("0") }
    var resolveWeek by rememberSaveable { mutableStateOf("0") }
    var uncertain by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    if(sharing.isNotBlank()) {OwnerGuestSharingScreen(sharing,{sharing=""},onRequest);return}
    suspend fun refresh() {
        state=onRequest("guests",null)
        val tasks=onRequest("guests/tasks",null).optJSONArray("tasks")
        uncertain=if(tasks==null) emptyList() else (0 until tasks.length()).map {tasks.getJSONObject(it)}.filter {it.optString("state")=="uncertain"}
    }
    LaunchedEffect(Unit) {busy=true;try{refresh()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}
    resolution?.let {task->AlertDialog(onDismissRequest={if(!busy)resolution=null},title={Text("Согласовать расход")},text={Column(Modifier.verticalScroll(rememberScrollState())) {
        Text("Исход задачи неизвестен. Укажите дополнительный расход сверх уже учтённого; 0 означает, что вы не списываете дополнительный расход. Повторный запуск этой задачи запрещён.")
        OutlinedTextField(resolveFive,{resolveFive=it},enabled=!busy,label={Text("5 часов, п.п.")})
        OutlinedTextField(resolveWeek,{resolveWeek=it},enabled=!busy,label={Text("Неделя, п.п.")})
    }},confirmButton={TextButton(enabled=!busy,onClick={busy=true;scope.launch {try{
        val usage=JSONObject().put("fiveHours",resolveFive.replace(',','.').toDouble()).put("week",resolveWeek.replace(',','.').toDouble())
        onRequest("guests/resolve",JSONObject().put("operationId",task.getString("operationId")).put("confirmed",true).put("additionalUsage",usage));resolution=null;refresh()
    }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Подтвердить")}},dismissButton={TextButton(enabled=!busy,onClick={resolution=null}){Text("Отмена")}})}
    if(revoke.isNotBlank()) AlertDialog(onDismissRequest={revoke=""},title={Text("Отозвать доступ?")},text={Text("Сессия гостя перестанет работать. Чаты и файлы не удаляются.")},
        confirmButton={TextButton(onClick={val id=revoke;revoke="";busy=true;scope.launch {try{
            onRequest("guests/action",JSONObject().put("action","revoke").put("guestId",id).put("operationId",UUID.randomUUID().toString()));refresh()
        }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Отозвать")}},dismissButton={TextButton(onClick={revoke=""}){Text("Отмена")}})
    Surface(Modifier.fillMaxSize()) {
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
        UiScreenHeader("Гостевой доступ",onClose)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal=24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
            Text("Приглашения и независимые бюджеты",style=MaterialTheme.typography.titleLarge)
            Text("Гостевые задачи выполняются отдельно. На Linux для них требуется закрыть окно Desktop; ваши задачи через приложение имеют приоритет.",color=MaterialTheme.colorScheme.onSurfaceVariant)
            val runtime=state?.optJSONObject("runtime")
            if(runtime?.optBoolean("supported")==true) TextButton(enabled=!busy,onClick={busy=true;scope.launch{try{
                onRequest("guests/runtime",JSONObject().put("enabled",!runtime.optBoolean("enabled")));refresh()
            }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}) {Text(if(runtime.optBoolean("enabled")) "Выключить гостевой запуск" else "Включить гостевой запуск")}
            val profiles=context.getSharedPreferences("guest-profiles",android.content.Context.MODE_PRIVATE)
            profiles.all.forEach {(id,uri)->
                val saved=context.getSharedPreferences("guest-$id",android.content.Context.MODE_PRIVATE)
                TextButton(onClick={onOpenGuest(uri.toString())}) {Text("Гостевой чат: "+saved.getString("name","Подключение"))}
            }
            if(busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if(error.isNotBlank()) Text(error,color=MaterialTheme.colorScheme.error)
            if(form) {
                OutlinedTextField(name,{name=it;pending=""},enabled=!busy&&pending.isBlank()&&editing.isBlank(),label={Text("Имя гостя")},singleLine=true,modifier=Modifier.fillMaxWidth())
                if(reallocating) {
                    Text("Явное пополнение обнуляет учтённый расход только выбранных окон.")
                    Row {Checkbox(fiveSelected,{fiveSelected=it;pending=""},enabled=!busy&&pending.isBlank());Text("Пополнить 5 часов")}
                    Row {Checkbox(weekSelected,{weekSelected=it;pending=""},enabled=!busy&&pending.isBlank());Text("Пополнить неделю")}
                }
                QuotaEditor("5 часов",five,enabled=!busy&&pending.isBlank()&&(!reallocating||fiveSelected)){five=it;pending=""}
                QuotaEditor("Неделя",week,enabled=!busy&&pending.isBlank()&&(!reallocating||weekSelected)){week=it;pending=""}
                Text("Смена размера сохраняет учтённый расход. Счётчик Codex приблизительный: возможна задержка остановки после достижения бюджета.",style=MaterialTheme.typography.bodySmall)
                Button(enabled=!busy&&name.isNotBlank(),onClick={busy=true;error="";scope.launch{try{
                    if(pending.isBlank()) {
                        fun normalize(raw:String)=JSONObject(raw).also {q->if(q.optString("mode")!="unlimited"){
                            val amount=q.optString("amount").replace(',','.').toDoubleOrNull() ?: error("Введите числовой размер квоты")
                            require(amount>0&&amount<=100){"Квота должна быть больше 0 и не больше 100%"};q.put("amount",amount)
                        }}
                        pending=JSONObject().put("operationId",UUID.randomUUID().toString()).put("secret",newGuestSecret()).put("name",name)
                            .put("quotas",JSONObject().also {q->
                                if(!reallocating||fiveSelected) q.put("fiveHours",normalize(five))
                                if(!reallocating||weekSelected) q.put("week",normalize(week))
                                require(q.length()>0){"Выберите окно для пополнения"}
                            }).also { if(editing.isNotBlank()) it.put("action",if(reallocating) "reallocate" else "configure").put("guestId",editing) }.toString()
                    }
                    val request=JSONObject(pending)
                    val result=onRequest(if(editing.isBlank()) "guests/invite" else "guests/action",request)
                    if(editing.isBlank()&&result.has("expiresAt")&&result.optLong("expiresAt")<=System.currentTimeMillis()/1000) {
                        pending="";link="";refresh();error("Срок приглашения истёк. Нажмите «Создать приглашение», чтобы получить новое.")
                    }
                    if(editing.isBlank()) link=Uri.Builder().scheme("codexphone").authority("invite").appendQueryParameter("host",host).appendQueryParameter("fingerprint",fingerprint)
                        .appendQueryParameter("secret",request.getString("secret")).build().toString()
                    pending="";form=false;editing="";reallocating=false;refresh()
                }catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text(if(pending.isNotBlank()) "Повторить запрос" else if(editing.isBlank()) "Создать приглашение" else if(reallocating) "Подтвердить пополнение" else "Сохранить квоты")}
                TextButton(onClick={form=false;pending="";editing=""},enabled=!busy){Text("Отмена")}
            } else TextButton(onClick={editing="";reallocating=false;form=true},enabled=!busy){Text("Пригласить гостя")}
            if(link.isNotBlank()) {
                Text("Одноразовое приглашение действует 30 минут.")
                qr?.let {bitmap->androidx.compose.foundation.Image(bitmap=bitmap.asImageBitmap(),contentDescription="QR-код приглашения",modifier=Modifier.size(240.dp))}
                Button(onClick={context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT,link),"Отправить приглашение"))}){Text("Поделиться приглашением")}
            }
            val guests=state?.optJSONArray("guests")
            if(guests!=null) for(i in 0 until guests.length()) {
                val guest=guests.getJSONObject(i)
                Text(guest.getString("name"),style=MaterialTheme.typography.titleMedium)
                for((key,label) in listOf("fiveHours" to "5 часов","week" to "Неделя")) {
                    val budget=guest.getJSONObject("quotas").getJSONObject(key)
                    Text(if(budget.getJSONObject("rule").optString("mode")=="unlimited") "$label: без ограничения"
                        else "$label: осталось ${budget.optDouble("remaining")} из ${budget.optDouble("allocated")} п.п.",color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick={reallocating=false;editing=guest.getString("id");name=guest.getString("name")
                    fun editable(key:String)=guest.getJSONObject("quotas").getJSONObject(key).getJSONObject("rule").let {q->
                        JSONObject(q.toString()).put("basis",q.optString("basis","full")).put("amount",q.optDouble("amount",15.0)).toString()
                    }
                    five=editable("fiveHours");week=editable("week");pending="";form=true
                },enabled=!busy) {Text("Настроить квоты")}
                TextButton(onClick={editing=guest.getString("id");name=guest.getString("name");reallocating=true;pending="";fiveSelected=true;weekSelected=true
                    five=guest.getJSONObject("quotas").getJSONObject("fiveHours").getJSONObject("rule").toString()
                    week=guest.getJSONObject("quotas").getJSONObject("week").getJSONObject("rule").toString();form=true
                },enabled=!busy){Text("Выделить квоту заново")}
                TextButton(onClick={sharing=guest.getString("id")},enabled=!busy){Text("Проекты, чаты и изменения")}
                TextButton(onClick={revoke=guest.getString("id")},enabled=!busy){Text("Отозвать доступ")}
            }
            uncertain.forEach {task->
                Text("Требует проверки: "+task.optString("text").take(80))
                TextButton(onClick={resolution=task;resolveFive="0";resolveWeek="0"}){Text("Согласовать расход и освободить очередь")}
            }
            TextButton(enabled=!busy,onClick={busy=true;scope.launch{try{refresh();error=""}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Обновить")}
            Spacer(Modifier.height(24.dp))
        }
    }
    }
}
