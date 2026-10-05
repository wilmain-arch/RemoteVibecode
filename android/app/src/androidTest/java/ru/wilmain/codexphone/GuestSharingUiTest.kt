package ru.wilmain.codexphone

import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.json.JSONArray
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class GuestSharingUiTest {
    @get:Rule val compose=createComposeRule()
    @Test fun ownerSeesWorkCopyBeforeAnyGuestTaskExists() {
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            OwnerGuestSharingScreen("fixture-guest",{}) {path,_->when {
                path=="projects"->JSONObject().put("projects",JSONArray())
                path=="guests"->JSONObject().put("copiesListingAvailable",true).put("guests",JSONArray().put(JSONObject().put("id","fixture-guest").put("grants",JSONArray())))
                path.startsWith("guests/copies")->JSONObject().put("copies",JSONArray().put(JSONObject().put("id","fixture-copy-new"))).put("nextCursor",JSONObject.NULL)
                else->error("History of tasks must not be used to enumerate copies")
            }}
        }}
        compose.onNodeWithText("Просмотреть копию fixture-").performScrollTo().assertExists()
        compose.onNodeWithText("Обновите агент",substring=true).assertDoesNotExist()
    }

    @Test fun ownerAcceptsOnlyCapturedCopyAfterExplicitConfirmation() {
        val applied=mutableListOf<JSONObject>()
        compose.setContent {MaterialTheme(colorScheme=lightPalette,typography=appTypography) {
            OwnerGuestSharingScreen("fixture-guest",{}) {path,request->when(path) {
                "projects"->JSONObject().put("projects",JSONArray())
                "guests"->JSONObject().put("guests",JSONArray().put(JSONObject().put("id","fixture-guest").put("grants",JSONArray())))
                "guests/tasks"->JSONObject().put("tasks",JSONArray().put(JSONObject().put("guestId","fixture-guest").put("scopeId","fixture-copy")))
                "guests/review"->when {
                    request?.optBoolean("confirmed")==true->{applied.add(JSONObject(request.toString()));JSONObject().put("applied",true)}
                    request?.has("path")==true->JSONObject().put("diff","-old\n+new").put("guestHash","synthetic-hash")
                    else->JSONObject().put("changes",JSONArray().put(JSONObject().put("path","demo.txt")))
                }
                else->error("Unexpected synthetic route")
            }}
        }}
        compose.onNodeWithText("Просмотреть копию fixture-").performScrollTo().performClick()
        compose.onNodeWithText("Посмотреть diff").performScrollTo().performClick()
        compose.onNodeWithText("Принять изменение этого файла").performScrollTo().performClick()
        compose.onNodeWithText("Применить изменение?").assertExists()
        assertTrue(applied.isEmpty())
        compose.onNodeWithText("Отмена").performClick()
        assertTrue(applied.isEmpty())
        compose.onNodeWithText("Принять изменение этого файла").performScrollTo().performClick()
        compose.onNodeWithText("Применить").performClick()
        compose.waitUntil(5000){applied.size==1}
        assertEquals("fixture-copy",applied.single().getString("scopeId"))
        assertEquals("demo.txt",applied.single().getString("path"))
        assertEquals("synthetic-hash",applied.single().getString("guestHash"))
    }
}
