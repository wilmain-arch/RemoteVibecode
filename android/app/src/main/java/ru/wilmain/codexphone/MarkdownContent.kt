package ru.wilmain.codexphone

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.ClickableText
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val inlinePattern = Regex("\\*\\*([^*]+)\\*\\*|`([^`]+)`|\\[([^]]+)]\\(([^)]+)\\)|\\*([^*]+)\\*")
private val tableDivider = Regex("^\\|?[ :|\\-]+\\|?$")

@Composable
private fun InlineMarkdown(value: String, modifier: Modifier = Modifier,
                           color: Color = MaterialTheme.colorScheme.onSurface,
                           bold: Boolean = false, compact: Boolean = false) {
    val uriHandler = LocalUriHandler.current
    val clipboard = LocalClipboardManager.current
    val builder = AnnotatedString.Builder()
    var from = 0
    inlinePattern.findAll(value).forEach { match ->
        builder.append(value.substring(from, match.range.first))
        when {
            match.groups[1] != null -> {
                val start = builder.length
                builder.append(match.groups[1]!!.value)
                builder.addStyle(SpanStyle(fontWeight = FontWeight.SemiBold), start, builder.length)
            }
            match.groups[2] != null -> {
                val start = builder.length
                builder.append(match.groups[2]!!.value)
                builder.addStyle(SpanStyle(fontFamily = FontFamily.Monospace,
                    background = MaterialTheme.colorScheme.surfaceVariant), start, builder.length)
            }
            match.groups[3] != null -> {
                val start = builder.length
                builder.append(match.groups[3]!!.value)
                val url = match.groups[4]!!.value
                if (url.startsWith("https://") || url.startsWith("http://") || url.startsWith("mailto:")) {
                    builder.addStringAnnotation("url", url, start, builder.length)
                    builder.addStyle(SpanStyle(color = MaterialTheme.colorScheme.primary,
                        textDecoration = TextDecoration.Underline), start, builder.length)
                } else {
                    builder.addStringAnnotation("path", url, start, builder.length)
                    builder.addStyle(SpanStyle(fontWeight = FontWeight.Medium,
                        textDecoration = TextDecoration.Underline), start, builder.length)
                }
            }
            match.groups[5] != null -> {
                val start = builder.length
                builder.append(match.groups[5]!!.value)
                builder.addStyle(SpanStyle(fontStyle = FontStyle.Italic), start, builder.length)
            }
        }
        from = match.range.last + 1
    }
    builder.append(value.substring(from))
    ClickableText(builder.toAnnotatedString(), modifier = modifier,
        style = (if (compact) MaterialTheme.typography.bodySmall else MaterialTheme.typography.bodyLarge).copy(color = color,
            fontWeight = if (bold) FontWeight.SemiBold else FontWeight.Normal,
            lineHeight = if (compact) 19.sp else 25.sp),
        onClick = { offset ->
            builder.toAnnotatedString().getStringAnnotations("url", offset, offset).firstOrNull()?.let {
                uriHandler.openUri(it.item)
            }
            builder.toAnnotatedString().getStringAnnotations("path", offset, offset).firstOrNull()?.let {
                clipboard.setText(AnnotatedString(it.item))
            }
        })
}

private fun tableCells(line: String): List<String> = line.trim().trim('|').split('|').map { it.trim() }

@Composable
internal fun MarkdownContent(markdown: String, modifier: Modifier = Modifier, compact: Boolean = false) {
    val lines = markdown.trim().lines()
    Column(modifier, verticalArrangement = Arrangement.spacedBy(if (compact) 7.dp else 9.dp)) {
        var index = 0
        while (index < lines.size) {
            val line = lines[index]
            when {
                line.isBlank() -> index++
                line.trimStart().startsWith("```") -> {
                    val language = line.trim().removePrefix("```").trim()
                    val code = StringBuilder()
                    index++
                    while (index < lines.size && !lines[index].trimStart().startsWith("```")) {
                        if (code.isNotEmpty()) code.append('\n')
                        code.append(lines[index])
                        index++
                    }
                    if (index < lines.size) index++
                    Surface(shape = RoundedCornerShape(12.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            if (language.isNotBlank()) Text(language,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.labelSmall)
                            Text(code.toString(), Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                                .padding(top = if (language.isBlank()) 0.dp else 8.dp),
                                fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                                color = MaterialTheme.colorScheme.onSurface)
                        }
                    }
                }
                line.contains('|') && index + 1 < lines.size && tableDivider.matches(lines[index + 1].trim()) -> {
                    val rows = mutableListOf(tableCells(line))
                    index += 2
                    while (index < lines.size && lines[index].contains('|') && lines[index].isNotBlank()) {
                        rows.add(tableCells(lines[index])); index++
                    }
                    val columnCount = rows.maxOfOrNull { it.size } ?: 0
                    if (compact && columnCount > 2) Text("Сдвиньте таблицу →",
                        Modifier.fillMaxWidth(),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = androidx.compose.ui.text.style.TextAlign.End)
                    Surface(shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        contentColor = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.horizontalScroll(rememberScrollState()).padding(8.dp)) {
                            rows.forEachIndexed { rowIndex, cells ->
                                Row {
                                    repeat(columnCount) { column ->
                                        InlineMarkdown(cells.getOrElse(column) { "" },
                                            Modifier.width(if (compact) 126.dp else 150.dp)
                                                .padding(horizontal = 8.dp, vertical = if (compact) 6.dp else 5.dp),
                                            bold = rowIndex == 0, compact = compact)
                                    }
                                }
                            }
                        }
                    }
                }
                line.startsWith("# ") || line.startsWith("## ") || line.startsWith("### ") -> {
                    val level = line.takeWhile { it == '#' }.length
                    Text(line.drop(level).trim(),
                        style = when (level) {
                            1 -> if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge
                            2 -> if (compact) MaterialTheme.typography.titleSmall else MaterialTheme.typography.titleMedium
                            else -> if (compact) MaterialTheme.typography.bodyLarge else MaterialTheme.typography.titleSmall
                        }, fontWeight = FontWeight.SemiBold)
                    index++
                }
                line.trimStart().startsWith("> ") -> {
                    Surface(shape = RoundedCornerShape(4.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant) {
                        InlineMarkdown(line.trimStart().removePrefix("> "),
                            Modifier.fillMaxWidth().padding(start = 12.dp, top = 8.dp, end = 8.dp, bottom = 8.dp),
                            compact = compact)
                    }
                    index++
                }
                line.trimStart().startsWith("- ") || line.trimStart().startsWith("* ") ||
                    Regex("^\\s*\\d+[.)] ").containsMatchIn(line) -> {
                    val trimmed = line.trimStart()
                    val marker = if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) "•"
                        else trimmed.takeWhile { it.isDigit() } + "."
                    val content = if (marker == "•") trimmed.drop(2) else trimmed.substringAfter(' ')
                    Row(Modifier.fillMaxWidth()) {
                        Text(marker, Modifier.width(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        InlineMarkdown(content, Modifier.weight(1f), compact = compact)
                    }
                    index++
                }
                else -> {
                    val paragraph = StringBuilder(line)
                    index++
                    while (index < lines.size && lines[index].isNotBlank() &&
                        !lines[index].startsWith("#") && !lines[index].trimStart().startsWith("```") &&
                        !lines[index].trimStart().startsWith("- ") && !lines[index].trimStart().startsWith("> ")) {
                        paragraph.append('\n').append(lines[index]); index++
                    }
                    InlineMarkdown(paragraph.toString(), Modifier.fillMaxWidth(), compact = compact)
                }
            }
        }
    }
}
