package ru.wilmain.codexphone

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.json.JSONObject

private data class AdbDevice(val id: String, val name: String, val address: String,
    val transport: String, val port: String, val status: String, val model: String,
    val lastSeenAt: Long, val lastError: String)

@Composable
internal fun AdbDevicesScreen(
    onClose: () -> Unit,
    onRequest: suspend (String, JSONObject?) -> JSONObject,
) {
    val scope = rememberCoroutineScope()
    var devices by remember { mutableStateOf<List<AdbDevice>>(emptyList()) }
    var events by remember { mutableStateOf<List<String>>(emptyList()) }
    var relayAvailable by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf("") }
    var message by remember { mutableStateOf("") }
    var adding by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var transport by remember { mutableStateOf("ssh_relay") }
    var connectPort by remember { mutableStateOf("") }
    var pairingPort by remember { mutableStateOf("") }
    var pairingCode by remember { mutableStateOf("") }

    suspend fun refresh() {
        val response = onRequest("devices", null)
        relayAvailable = response.optBoolean("relayAvailable")
        val array = response.optJSONArray("devices")
        devices = if (array == null) emptyList() else (0 until array.length()).mapNotNull { index ->
            array.optJSONObject(index)?.let { item ->
                AdbDevice(item.optString("id"), item.optString("name"), item.optString("address"),
                    item.optString("transport"), item.optString("connectPort"),
                    item.optString("status"), item.optString("model"),
                    item.optLong("lastSeenAt"), item.optString("lastError"))
            }
        }
        val eventArray = response.optJSONArray("events")
        events = if (eventArray == null) emptyList() else (0 until eventArray.length()).mapNotNull { index ->
            eventArray.optJSONObject(index)?.let { item ->
                val time = java.text.SimpleDateFormat("dd.MM HH:mm:ss", java.util.Locale.getDefault())
                    .format(java.util.Date(item.optLong("time") * 1000))
                "$time · ${item.optString("name")} · ${if (item.optString("status") == "connected") "подключено" else "нет связи"}" +
                    item.optString("detail").takeIf { it.isNotBlank() }?.let { " — $it" }.orEmpty()
            }
        }
    }

    fun runAction(action: suspend () -> Unit) {
        scope.launch {
            loading = true
            error = ""
            message = ""
            try { action(); refresh() }
            catch (exc: Exception) { error = exc.message ?: "Не удалось выполнить действие" }
            finally { loading = false }
        }
    }

    LaunchedEffect(Unit) { runAction { refresh() } }
    val handleBack = {
        when {
            adding -> adding = false
            selected.isNotBlank() -> selected = ""
            else -> onClose()
        }
    }
    BackHandler(onBack = handleBack)
    val current = devices.firstOrNull { it.id == selected }
    Scaffold(containerColor = MaterialTheme.colorScheme.background, topBar = {
        UiScreenHeader(
            title = if (adding) "Новое устройство" else if (current != null) current.name else "Устройства ADB",
            onBack = handleBack,
        )
    }) { inner ->
        Column(Modifier.fillMaxSize().padding(inner).imePadding().verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium)
            if (message.isNotBlank()) Text(message, color = MaterialTheme.colorScheme.secondary,
                style = MaterialTheme.typography.bodyMedium)

            if (!adding && current == null) {
                Text("Подключённые телефоны", style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold)
                Text("Устройство появится на ПК в adb devices после подключения. Беспроводную отладку включает владелец телефона в настройках Android.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (loading && devices.isEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                        Text("Загружаю список устройств…", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else if (!loading && devices.isEmpty() && error.isNotBlank()) {
                    TextButton(onClick = { runAction { refresh() } }, enabled = !loading) {
                        UiGlyph(UiIcon.Refresh, size = 18.dp)
                        Text("Повторить загрузку", modifier = Modifier.padding(start = 6.dp))
                    }
                } else if (!loading && devices.isEmpty()) {
                    Text("Сохранённых устройств пока нет",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodyMedium)
                }
                devices.forEach { device ->
                    Surface(Modifier.fillMaxWidth().clickable {
                        selected = device.id; connectPort = device.port; pairingCode = ""; pairingPort = ""
                    }, color = MaterialTheme.colorScheme.surfaceVariant,
                        shape = RoundedCornerShape(20.dp)) {
                        Column(Modifier.padding(18.dp)) {
                            Text(device.name, fontWeight = FontWeight.SemiBold)
                            Text("${device.address}:${device.port} · ${if (device.status == "connected") "Подключено" else "Не в сети"}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (device.lastError.isNotBlank()) Text(device.lastError,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
                Button(onClick = { adding = true; name = ""; address = ""; connectPort = "";
                    transport = if (relayAvailable) "ssh_relay" else "direct" },
                    modifier = Modifier.fillMaxWidth()) { Text("Добавить устройство") }
                TextButton(onClick = { runAction { refresh() } }, enabled = !loading) {
                    Text(if (loading) "Обновляю…" else "Обновить состояния")
                }
                if (events.isNotEmpty()) Section("История соединения") {
                    events.take(8).forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
                }
            }

            if (adding) {
                Section("1 · Подготовка телефона") {
                    Text("Откройте Настройки → Для разработчиков → Беспроводная отладка. Включите её и разрешите текущую сеть Wi‑Fi. Адрес и порт скопируйте с этого экрана.")
                    Text("Для доступа извне включите на телефоне свою VPN-сеть и используйте её адрес. Телефон должен принимать входящие соединения от ПК или ретранслятора.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Section("2 · Сетевой путь") {
                    Column(Modifier.selectableGroup(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        TransportChoice("Через домашний сервер", "ssh_relay", transport,
                            enabled = relayAvailable) { transport = it }
                        TransportChoice("Напрямую с ПК", "direct", transport) { transport = it }
                    }
                    if (!relayAvailable) Text("SSH-ретранслятор не настроен на ПК. Доступно прямое подключение.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Section("3 · Данные устройства") {
                    OutlinedTextField(name, { name = it }, label = { Text("Название") },
                        placeholder = { Text("Например, OnePlus 9 Pro") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(address, { address = it.trim() }, label = { Text("IP-адрес телефона") },
                        placeholder = { Text("100.110.108.65") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(connectPort, { connectPort = it.filter(Char::isDigit) },
                        label = { Text("Порт на основном экране отладки") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    Button(onClick = { runAction {
                        val result = onRequest("devices", JSONObject().put("name", name)
                            .put("address", address).put("connectPort", connectPort)
                            .put("transport", transport))
                        selected = result.getString("id"); adding = false
                        message = "Устройство сохранено. Теперь выполните сопряжение или подключение."
                    } }, enabled = !loading && name.isNotBlank() && address.isNotBlank() && connectPort.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()) { Text("Сохранить и продолжить") }
                }
            }

            if (current != null && !adding) {
                Text("${current.address} · ${if (current.transport == "ssh_relay") "Через SSH-сервер" else "Напрямую"}",
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (current.model.isNotBlank()) Text("Модель: ${current.model}")
                if (current.lastSeenAt > 0) Text("Последняя связь: " +
                    java.text.SimpleDateFormat("dd.MM HH:mm:ss", java.util.Locale.getDefault())
                        .format(java.util.Date(current.lastSeenAt * 1000)),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (current.lastError.isNotBlank()) Text("Причина: ${current.lastError}",
                    color = MaterialTheme.colorScheme.error)
                Section("1 · Порт подключения") {
                    Text("На телефоне откройте «Беспроводная отладка». Порт под надписью «IP-адрес и порт» может измениться после выключения отладки или смены сети.")
                    OutlinedTextField(connectPort, { connectPort = it.filter(Char::isDigit) },
                        label = { Text("Текущий порт подключения") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    TextButton(onClick = { runAction {
                        onRequest("port", JSONObject().put("id", current.id)
                            .put("connectPort", connectPort))
                        message = "Порт сохранён"
                    } }, enabled = !loading && connectPort.isNotBlank()) { Text("Сохранить порт") }
                }
                Section("2 · Сопряжение") {
                    Text("Нужно один раз для каждого ПК. На телефоне нажмите «Подключить устройство с помощью кода сопряжения» и оставьте окно открытым.")
                    Text("Введите порт из всплывающего окна — он отличается от основного порта выше.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedTextField(pairingPort, { pairingPort = it.filter(Char::isDigit) },
                        label = { Text("Порт сопряжения") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(pairingCode, { pairingCode = it.filter(Char::isDigit).take(6) },
                        label = { Text("Шестизначный код") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth())
                    TextButton(onClick = { runAction {
                        val result = onRequest("pair", JSONObject().put("id", current.id)
                            .put("pairingPort", pairingPort).put("code", pairingCode))
                        pairingCode = ""; message = result.optString("message", "Сопряжение выполнено")
                    } }, enabled = !loading && pairingPort.isNotBlank() && pairingCode.length == 6) {
                        Text("Сопрячь с ПК")
                    }
                    Text("Если этот ПК уже есть в списке сопряжённых устройств, переходите к подключению.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Section("3 · Подключение") {
                    Button(onClick = { runAction {
                        if (connectPort != current.port) onRequest("port", JSONObject().put("id", current.id)
                            .put("connectPort", connectPort))
                        onRequest("connect", JSONObject().put("id", current.id))
                        message = "ADB подключён: устройство видно на ПК"
                    } }, enabled = !loading && connectPort.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()) {
                        Text(if (loading) "Подключаю…" else "Подключить и проверить")
                    }
                    Text("После подтверждения статуса «Подключено» ПК сможет устанавливать APK и читать логи через ADB.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = { runAction {
                    onRequest("delete", JSONObject().put("id", current.id))
                    selected = ""; message = "Устройство удалено из списка"
                } }, enabled = !loading) { Text("Удалить устройство") }
            }
            Spacer(Modifier.height(20.dp))
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(20.dp),
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
            content()
        }
    }
}

@Composable
private fun TransportChoice(label: String, value: String, selected: String,
    enabled: Boolean = true, onSelect: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().background(
        if (selected == value) MaterialTheme.colorScheme.secondaryContainer
        else MaterialTheme.colorScheme.background, RoundedCornerShape(14.dp))
        .selectable(selected = selected == value, enabled = enabled, role = Role.RadioButton) {
            onSelect(value)
        }.heightIn(min = 48.dp).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically) {
        UiGlyph(if (selected == value) UiIcon.CircleCheck else UiIcon.Circle,
            size = 19.dp, tint = if (selected == value) MaterialTheme.colorScheme.secondary
                else MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(8.dp))
        Text(label, color = if (enabled) MaterialTheme.colorScheme.onSurface
            else MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
