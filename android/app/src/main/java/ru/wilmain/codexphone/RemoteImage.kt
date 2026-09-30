package ru.wilmain.codexphone

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale

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
) {
    val key = endpoint + if (thumbnail) "&size=thumb" else ""
    var bitmap by remember(key) { mutableStateOf(imageCache.get(key)) }
    var failed by remember(key) { mutableStateOf(false) }
    LaunchedEffect(key) {
        if (bitmap == null) {
            runCatching {
                val bytes = loadImage(key)
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                require(bounds.outWidth > 0 && bounds.outHeight > 0) { "Неподдерживаемое изображение" }
                val target = if (thumbnail) 160 else 2048
                var sample = 1
                while (bounds.outWidth / sample > target * 2 || bounds.outHeight / sample > target * 2) sample *= 2
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
                    BitmapFactory.Options().apply { inSampleSize = sample })
                    ?: error("Не удалось открыть изображение")
            }.onSuccess {
                imageCache.put(key, it)
                bitmap = it
            }.onFailure { failed = true }
        }
    }
    Box(modifier, contentAlignment = Alignment.Center) {
        when {
            bitmap != null -> Image(bitmap!!.asImageBitmap(), contentDescription = description,
                modifier = Modifier.fillMaxSize(),
                contentScale = if (thumbnail) ContentScale.Crop else ContentScale.Fit)
            failed -> Text("Изображение недоступно")
            else -> CircularProgressIndicator()
        }
    }
}
