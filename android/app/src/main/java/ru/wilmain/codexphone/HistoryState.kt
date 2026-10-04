package ru.wilmain.codexphone

import org.json.JSONObject

/** The newest page is authoritative; retain only older turns still owned by Desktop. */
internal fun mergeHistorySnapshot(previous: List<ChatLine>, latest: List<ChatLine>, snapshot: JSONObject): List<ChatLine> {
    if (latest.isEmpty() || !snapshot.optBoolean("hasMore")) return latest
    val valid = snapshot.optJSONArray("existingTurnIds")?.let { ids ->
        (0 until ids.length()).mapTo(HashSet()) { ids.optString(it) }
    }
    val validMessages = snapshot.optJSONArray("existingMessageIds")?.let { ids ->
        (0 until ids.length()).mapTo(HashSet()) { ids.optString(it) }
    }
    val boundary = previous.indexOfFirst { it.id == latest.first().id }
    val older = if (boundary >= 0) previous.take(boundary) else if (valid != null) previous else emptyList()
    val latestIds = latest.mapTo(HashSet()) { it.id }
    return older.filter { it.id !in latestIds && (valid == null || it.turnId in valid) && (validMessages == null || it.id in validMessages) } + latest
}
