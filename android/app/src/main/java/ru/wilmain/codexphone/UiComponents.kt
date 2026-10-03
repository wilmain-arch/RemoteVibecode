package ru.wilmain.codexphone

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.compose.ui.window.DialogWindowProvider
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

internal object UiSpace {
    val screen = 24.dp
    val gap = 8.dp
    val target = 48.dp
    val panel = RoundedCornerShape(16.dp)
    val composer = RoundedCornerShape(24.dp)
}

@Composable
internal fun CopyAction(text: String, description: String = "Копировать сообщение") {
    val clipboard = LocalClipboardManager.current
    var copied by remember(text) { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { kotlinx.coroutines.delay(1800); copied = false } }
    IconButton(onClick = { clipboard.setText(AnnotatedString(text)); copied = true },
        modifier = Modifier.size(UiSpace.target).semantics {
            contentDescription = if (copied) "Скопировано" else description
            liveRegion = LiveRegionMode.Polite
        }) {
        UiGlyph(if (copied) UiIcon.Check else UiIcon.Copy, size = 17.dp,
            tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
internal fun ImageViewer(endpoint: String, name: String, loadImage: suspend (String) -> ByteArray,
    onClose: () -> Unit, onSave: () -> Unit, saveStatus: String = "") {
    var saveRequested by remember(endpoint) { mutableStateOf(false) }
    val relevantSaveStatus = saveStatus.takeIf {
        it.contains(name) || it.contains("сохран", ignoreCase = true) || it.contains("скачив", ignoreCase = true)
    }.orEmpty()
    var scale by rememberSaveable(endpoint) { mutableFloatStateOf(1f) }
    var offset by remember(endpoint) { mutableStateOf(Offset.Zero) }
    val transform = rememberTransformableState { zoom, pan, _ ->
        scale = (scale * zoom).coerceIn(1f, 5f)
        offset = if (scale > 1f) offset + pan else Offset.Zero
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(
        usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        val view = LocalView.current
        val window = (view.parent as? DialogWindowProvider)?.window
        val lightBars = MaterialTheme.colorScheme.background.luminance() > 0.5f
        SideEffect { window?.let {
            WindowCompat.getInsetsController(it, view).apply {
                isAppearanceLightStatusBars = lightBars
                isAppearanceLightNavigationBars = lightBars
            }
        } }
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().safeDrawingPadding()) {
                UiScreenHeader(name, onClose, applyStatusInsets = false, actions = {
                    IconButton(onClick = { saveRequested = true; onSave() }, modifier = Modifier.semantics { contentDescription = "Скачать изображение" }) {
                        UiGlyph(UiIcon.Download, size = 22.dp)
                    }
                })
                Box(Modifier.weight(1f).fillMaxWidth().clipToBounds().transformable(transform)
                    .pointerInput(endpoint) { detectTapGestures(onDoubleTap = {
                        scale = if (scale > 1f) 1f else 2f; offset = Offset.Zero
                    }) }) {
                    RemoteImage(endpoint, name, loadImage, Modifier.fillMaxSize().graphicsLayer {
                        scaleX = scale; scaleY = scale
                        translationX = offset.x.coerceIn(-size.width * (scale - 1) / 2, size.width * (scale - 1) / 2)
                        translationY = offset.y.coerceIn(-size.height * (scale - 1) / 2, size.height * (scale - 1) / 2)
                    })
                }
                if (saveRequested && relevantSaveStatus.isNotBlank()) Text(relevantSaveStatus,
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp).semantics { liveRegion = LiveRegionMode.Polite },
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { scale = 1f; offset = Offset.Zero }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                    Text(if (scale > 1f) "Вернуть исходный размер" else "Разведите пальцы для увеличения",
                        style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

internal enum class ConnectionState { Connecting, Online, Reconnecting, Unpaired }

internal val ChatImageSaver = listSaver<ChatImage?, String>(
    save = { listOf(it?.id.orEmpty(), it?.name.orEmpty()) },
    restore = { if (it[0].isBlank()) null else ChatImage(it[0], it[1]) },
)

@Composable
internal fun rememberExpansionState(): androidx.compose.runtime.snapshots.SnapshotStateMap<String, Boolean> =
    rememberSaveable(saver = listSaver(
        save = { map: androidx.compose.runtime.snapshots.SnapshotStateMap<String, Boolean> -> map.entries.flatMap { listOf(it.key, it.value.toString()) } },
        restore = { keys -> mutableStateMapOf<String, Boolean>().apply { keys.chunked(2).forEach { put(it[0], it[1].toBoolean()) } } },
    )) { mutableStateMapOf<String, Boolean>() }
