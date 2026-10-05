package ru.wilmain.codexphone

import android.graphics.Bitmap
import android.util.Base64
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.json.JSONArray
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream

@RunWith(AndroidJUnit4::class)
class GuestWorkspaceUiTest {
    @get:Rule val compose=createComposeRule()
    @Test fun sharedProjectReadOnlyFilesHaveNoUploadOrWorkAction() {
        compose.setContent {MaterialTheme(colorScheme=darkPalette,typography=appTypography) {
            GuestSharedScreen({}, {}) {path,body->
                check(body==null)
                if(path=="guest/shared") JSONObject().put("resources",JSONArray().put(JSONObject()
                    .put("kind","project").put("resourceId","fixture-project").put("name","Проект друзей").put("right","view")
                    .put("threads",JSONArray().put(JSONObject().put("id","fixture-thread").put("name","Чат проекта")))))
                else JSONObject().put("path","").put("entries",JSONArray())
            }
        }}
        compose.onNodeWithText("Проект друзей").assertExists()
        compose.onNodeWithText("Чат проекта").assertExists()
        compose.onNodeWithText("Работать в моей копии").assertDoesNotExist()
        compose.onNodeWithText("Просмотреть файлы").performClick()
        compose.onNodeWithText("Общие файлы").assertExists()
        compose.onNodeWithText("Загрузить файл · до 4 МиБ").assertDoesNotExist()
    }
    @Test fun selectedImageRestoresAndHasDownloadAndZoomActions() {
        val output=ByteArrayOutputStream()
        val bitmap=Bitmap.createBitmap(8,8,Bitmap.Config.ARGB_8888)
        bitmap.compress(Bitmap.CompressFormat.PNG,100,output);bitmap.recycle()
        val data=Base64.encodeToString(output.toByteArray(),Base64.NO_WRAP)
        val restoration=StateRestorationTester(compose)
        restoration.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            GuestWorkspaceScreen("default",{},onRequest={path,_->
                if(path.contains("blob=true")) JSONObject().put("name","fixture.png").put("mime","image/png").put("dataBase64",data)
                else JSONObject().put("path","").put("entries",JSONArray().put(JSONObject().put("name","fixture.png").put("path","fixture.png").put("isDirectory",false)))
            })
        }}
        compose.onNodeWithText("fixture.png").performClick()
        compose.onNodeWithText("Сбросить масштаб").performScrollTo().assertExists()
        compose.onNodeWithText("Скачать файл").performScrollTo().assertExists()
        restoration.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("Сбросить масштаб").performScrollTo().assertExists()
        compose.onNodeWithContentDescription("fixture.png. Масштабирование двумя пальцами").assertExists()
    }
}
