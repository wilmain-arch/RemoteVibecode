package ru.wilmain.codexphone

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

internal fun isSupportedImage(name: String): Boolean = name.substringAfterLast('.', "").lowercase() in
    setOf("jpg", "jpeg", "png", "webp", "gif", "bmp")

private val imageCache = object : LruCache<String, Bitmap>(12 * 1024 * 1024) {
    override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
}

@Composable
internal fun RemoteImage(
    endpoint: String,
    description: String,
    loadImage: suspend (String) -> ByteArray,
    modifier: Modifier = Modifier,
    thumbnail: Boolean = false,
    adaptivePreview: Boolean = false,
) {
    val key = endpoint + if (thumbnail) "&size=thumb" else ""
    var bitmap by remember(key) { mutableStateOf(imageCache.get(key)) }
    var failure by remember(key) { mutableStateOf<String?>(null) }
    var retryToken by remember(key) { mutableIntStateOf(0) }
    LaunchedEffect(key, retryToken) {
        if (bitmap == null) {
            failure = null
            runCatching {
                val bytes = loadImage(key)
                withContext(Dispatchers.Default) {
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                require(bounds.outWidth > 0 && bounds.outHeight > 0) { "Неподдерживаемое изображение" }
                val target = if (thumbnail) 160 else 2048
                var sample = 1
                while (bounds.outWidth / sample > target * 2 || bounds.outHeight / sample > target * 2) sample *= 2
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
                    BitmapFactory.Options().apply { inSampleSize = sample })
                    ?: error("Не удалось открыть изображение")
                }
            }.onSuccess {
                imageCache.put(key, it)
                bitmap = it
            }.onFailure { if (it is CancellationException) throw it; failure = it.message?.takeIf(String::isNotBlank) ?: "Ошибка загрузки изображения" }
        }
    }
    BoxWithConstraints(modifier, contentAlignment = Alignment.Center) {
        val imageModifier = if (adaptivePreview && bitmap != null && maxWidth.value.isFinite() && maxHeight.value.isFinite()) {
            val ratio = bitmap!!.width.toFloat() / bitmap!!.height
            Modifier.fillMaxWidth().height((maxWidth / ratio).coerceIn(minHeight, maxHeight))
        } else Modifier.fillMaxSize()
        Box(if (adaptivePreview && bitmap != null) imageModifier else Modifier,
            contentAlignment = Alignment.Center) {

        when {
            bitmap != null -> Image(bitmap!!.asImageBitmap(), contentDescription = description,
                modifier = if (adaptivePreview) imageModifier else Modifier.fillMaxSize(),
                contentScale = if (thumbnail) ContentScale.Crop else ContentScale.Fit)
            failure != null && thumbnail -> Box(
                Modifier.fillMaxSize()
                    .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp))
                    .semantics { contentDescription = "Не удалось загрузить миниатюру $description. Нажмите, чтобы повторить" }
                    .clickable(onClickLabel = "Повторить загрузку изображения") { retryToken++ },
                contentAlignment = Alignment.Center,
            ) {
                UiGlyph(UiIcon.Refresh, size = 16.dp, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            failure != null -> Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    "Не удалось загрузить изображение${failure?.let { ": $it" }.orEmpty()}",
                    color = MaterialTheme.colorScheme.error,
                    textAlign = TextAlign.Center,
                )
                TextButton(onClick = { retryToken++ }) {
                    UiGlyph(UiIcon.Refresh, size = 18.dp)
                    Text("Повторить", modifier = Modifier.padding(start = 6.dp))
                }
            }
            else -> CircularProgressIndicator()
        }
    }
    }
}
