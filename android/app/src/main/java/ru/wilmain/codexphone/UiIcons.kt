package ru.wilmain.codexphone

import androidx.annotation.DrawableRes
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.size

internal enum class UiIcon(@DrawableRes val drawable: Int) {
    Menu(R.drawable.ui_menu),
    Files(R.drawable.ui_files),
    Plus(R.drawable.ui_plus),
    ArrowUp(R.drawable.ui_arrow_up),
    Back(R.drawable.ui_arrow_left),
    ChevronRight(R.drawable.ui_chevron_right),
    ChevronDown(R.drawable.ui_chevron_down),
    ChevronUp(R.drawable.ui_chevron_up),
    Refresh(R.drawable.ui_refresh_cw),
    Search(R.drawable.ui_search),
    Folder(R.drawable.ui_folder),
    File(R.drawable.ui_file),
    Image(R.drawable.ui_image),
    Terminal(R.drawable.ui_terminal),
    Globe(R.drawable.ui_globe),
    Sparkles(R.drawable.ui_sparkles),
    Check(R.drawable.ui_check),
    Close(R.drawable.ui_x),
    Upload(R.drawable.ui_upload),
    Download(R.drawable.ui_download),
    Paperclip(R.drawable.ui_paperclip),
    Circle(R.drawable.ui_circle),
    CircleCheck(R.drawable.ui_circle_check),
    Loader(R.drawable.ui_loader_circle),
    Message(R.drawable.ui_message_square),
    List(R.drawable.ui_list),
    Agents(R.drawable.ui_agents),
    Copy(R.drawable.ui_copy),
    Trash(R.drawable.ui_trash),
}

@Composable
internal fun UiGlyph(icon: UiIcon, description: String? = null, size: Dp = 20.dp,
    tint: Color = LocalContentColor.current) {
    Icon(painterResource(icon.drawable), contentDescription = description,
        modifier = Modifier.size(size), tint = tint)
}
