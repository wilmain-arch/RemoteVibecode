package ru.wilmain.codexphone

import android.content.Context
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.security.MessageDigest
import java.util.UUID

@Composable
internal fun GuestInvitationScreen(uri:Uri, onClose:()->Unit) {
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var data by remember(uri.toString()) {mutableStateOf<JSONObject?>(null)}
    var error by remember(uri.toString()) {mutableStateOf("")}
    var busy by remember(uri.toString()) {mutableStateOf(false)}
    var profileKey by remember(uri.toString()) {mutableStateOf("")}
    var connectionClient by remember(uri.toString()) {mutableStateOf<okhttp3.OkHttpClient?>(null)}
    var themeRevision by remember {mutableStateOf(0)}
    var chat by androidx.compose.runtime.saveable.rememberSaveable(uri.toString()) {mutableStateOf(false)}
    BackHandler { if(chat) chat=false else onClose() }
    suspend fun connect() {
        val host=uri.getQueryParameter("host").orEmpty()
        val pin=uri.getQueryParameter("fingerprint").orEmpty()
        val secret=uri.getQueryParameter("secret").orEmpty()
        require(uri.scheme=="codexphone"&&uri.host=="invite"&&Uri.parse(host).scheme=="https"&&pin.matches(Regex("[a-fA-F0-9]{64}"))&&(secret.length in 40..128||uri.getQueryParameter("profile")?.matches(Regex("[a-f0-9]{64}"))==true)){"Приглашение недействительно"}
        val profile=uri.getQueryParameter("profile") ?: MessageDigest.getInstance("SHA-256").digest((host+pin+secret).toByteArray()).joinToString(""){"%02x".format(it)}
        profileKey=profile
        val prefs=context.getSharedPreferences("guest-$profile",Context.MODE_PRIVATE)
        val device=prefs.getString("deviceId",null) ?: UUID.randomUUID().toString().also {prefs.edit().putString("deviceId",it).commit()}
        val token=readSavedToken(context,prefs).ifBlank {newGuestSecret().also {saveToken(context,prefs,it)}}
        prefs.edit().putString("host",host).putString("certificatePin",pin).commit()
        val client=connectionClient ?: pinnedClient(pin).also {connectionClient=it}
        data=withContext(Dispatchers.IO) {
            if (prefs.getString("guestId", "").isNullOrBlank()) {
            require(secret.length in 40..128) {"Приглашение не найдено. Откройте исходную ссылку."}
            val body=JSONObject().put("secret",secret).put("deviceId",device).put("sessionToken",token)
            val request=Request.Builder().url(host.trimEnd('/')+"/api/guest/redeem").post(body.toString().toRequestBody("application/json".toMediaType())).build()
            client.newCall(request).execute().use {response->
                val result=JSONObject(response.body?.string().orEmpty())
                if(!response.isSuccessful) error(result.optString("error","Не удалось принять приглашение"))
                prefs.edit().putString("guestId",result.getString("guestId")).commit()
            }
            }
            requestJson(client,host.trimEnd('/')+"/api/guest/self",token).also {result->
                val resume=Uri.Builder().scheme("codexphone").authority("invite").appendQueryParameter("host",host)
                    .appendQueryParameter("fingerprint",pin).appendQueryParameter("profile",profile).build().toString()
                val index=context.getSharedPreferences("guest-profiles",Context.MODE_PRIVATE)
                index.edit().putString(profile,resume).commit()
                prefs.edit().putString("name",result.optString("name")).commit()
            }
        }
    }
    LaunchedEffect(uri) {busy=true;try{connect()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}
    val themeMode=remember(themeRevision) {context.getSharedPreferences("companion",Context.MODE_PRIVATE).getString("themeMode","system")}
    val isDark=when(themeMode) {"dark"->true;"light"->false;else->androidx.compose.foundation.isSystemInDarkTheme()}
    SideEffect {
        (context as? android.app.Activity)?.window?.let {window->
            androidx.core.view.WindowCompat.getInsetsController(window,window.decorView).apply {
                isAppearanceLightStatusBars=!isDark;isAppearanceLightNavigationBars=!isDark
            }
        }
    }
    MaterialTheme(colorScheme=if(isDark) darkPalette else lightPalette,typography=appTypography) {
        Surface(Modifier.fillMaxSize()) {
        if(chat&&profileKey.isNotBlank()) {
            val prefs=context.getSharedPreferences("guest-$profileKey",Context.MODE_PRIVATE)
            CompositionLocalProvider(LocalImageScope provides "guest:$profileKey") {
            GuestChatScreen(data?.optString("name").orEmpty(),prefs.getString("draft","").orEmpty(),prefs.getString("pending","").orEmpty(),
                {draft,pending->prefs.edit().putString("draft",draft).putString("pending",pending).commit()}, {chat=false}, initialScope=prefs.getString("scope","default").orEmpty(),onScope={prefs.edit().putString("scope",it).commit()},onThemeChanged={themeRevision++}) {path,body->
                withContext(Dispatchers.IO) {
                    val root=prefs.getString("host","").orEmpty().trimEnd('/')
                    val client=connectionClient ?: pinnedClient(prefs.getString("certificatePin","").orEmpty()).also {connectionClient=it}
                    if(body==null) return@withContext requestJson(client,"$root/api/$path",readSavedToken(context,prefs))
                    val request=Request.Builder().url("$root/api/$path").header("Authorization","Bearer "+readSavedToken(context,prefs))
                    request.post(body.toString().toRequestBody("application/json".toMediaType()))
                    client.newCall(request.build()).execute().use {response->
                        val result=JSONObject(response.body?.string().orEmpty())
                        if(!response.isSuccessful) error(result.optString("error","Ошибка гостевого подключения"))
                        result
                    }
                }
            }
            }

        } else Surface(Modifier.fillMaxSize()) {
            Column(Modifier.statusBarsPadding().navigationBarsPadding()) {
                UiScreenHeader("Гостевое подключение",onClose)
                Column(Modifier.verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
                    if(busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                    if(error.isNotBlank()) {
                        Text(error,color=MaterialTheme.colorScheme.error)
                        Button(enabled=!busy,onClick={busy=true;error="";scope.launch{try{connect()}catch(e:Exception){if(e is kotlinx.coroutines.CancellationException) throw e;error=e.message.orEmpty()}finally{busy=false}}}){Text("Повторить")}
                    }
                    data?.let {guest->
                        Text(guest.optString("name"),style=MaterialTheme.typography.headlineMedium)
                        Text("Приглашение принято. Ваше основное подключение не изменилось.")
                        if(!guest.optBoolean("executionAvailable")) Text("Владелец ещё не включил гостевой запуск.",color=MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick={chat=true}){Text("Открыть мой чат")}
                        for((key,label) in listOf("fiveHours" to "5 часов","week" to "Неделя")) {
                            val quota=guest.getJSONObject("quotas").getJSONObject(key)
                            Text(if(quota.getJSONObject("rule").optString("mode")=="unlimited") "$label: без ограничения" else "$label: осталось ${quota.optDouble("remaining")} п.п.")
                        }
                    }
                    TextButton(onClick=onClose){Text("Вернуться в приложение")}
                }
            }
        }
    }
    }
}

@Composable
internal fun GuestConnectionsScreen(onOpen:(String)->Unit,onOwnerSetup:()->Unit) {
    val context=LocalContext.current
    val profiles=context.getSharedPreferences("guest-profiles",Context.MODE_PRIVATE)
    val mode=context.getSharedPreferences("companion",Context.MODE_PRIVATE).getString("themeMode","system")
    val dark=when(mode){"dark"->true;"light"->false;else->androidx.compose.foundation.isSystemInDarkTheme()}
    SideEffect {
        (context as? android.app.Activity)?.window?.let {window->
            androidx.core.view.WindowCompat.getInsetsController(window,window.decorView).apply {
                isAppearanceLightStatusBars=!dark;isAppearanceLightNavigationBars=!dark
            }
        }
    }
    MaterialTheme(colorScheme=if(dark) darkPalette else lightPalette,typography=appTypography) {
        Surface(Modifier.fillMaxSize()) {
            Column(Modifier.statusBarsPadding().navigationBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
                Text("Подключения",style=MaterialTheme.typography.headlineLarge)
                Text("Гостевые подключения сохранены. Выберите нужное, чтобы вернуться к своим чатам.")
                profiles.all.toSortedMap().forEach {(id,value)->
                    val uri=value as? String
                    if(uri!=null) {
                        val name=context.getSharedPreferences("guest-$id",Context.MODE_PRIVATE).getString("name","Гостевое подключение").orEmpty()
                        Button(onClick={onOpen(uri)},modifier=Modifier.fillMaxWidth().heightIn(min=48.dp)) {Text(name.ifBlank{"Гостевое подключение"})}
                    }
                }
                TextButton(onClick=onOwnerSetup){Text("Подключить свой компьютер")}
            }
        }
    }
}
