package com.justmylinks.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import org.json.JSONTokener
import java.io.IOException
import java.net.URLEncoder

data class SearchResult(
    val url: String,
    val title: String,
    val distance: Double? = null,
    val matchedTags: List<String> = emptyList()
)

data class SearchResponse(
    val query: String,
    val vector: List<SearchResult> = emptyList(),
    val title: List<SearchResult> = emptyList(),
    val tags: List<SearchResult> = emptyList()
)

class ApiClient(private val prefs: PreferencesRepository) {

    private val client = OkHttpClient()

    suspend fun saveDocument(url: String, html: String): Result<Unit> = withContext(Dispatchers.IO) {
        val apiUrl = prefs.apiUrl.trimEnd('/')
        val token = prefs.bearerToken
        if (apiUrl.isEmpty() || token.isEmpty()) {
            return@withContext Result.failure(
                IllegalStateException("API URL and bearer token must be configured in Settings")
            )
        }
        try {
            val body = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    "document",
                    "document.html",
                    html.toRequestBody("text/html".toMediaType())
                )
                .build()
            val request = Request.Builder()
                .url("$apiUrl/document?url=${URLEncoder.encode(url, "UTF-8")}")
                .put(body)
                .header("Authorization", "Bearer $token")
                .build()
            val response = client.newCall(request).execute()
            if (response.isSuccessful) {
                Result.success(Unit)
            } else {
                Result.failure(IOException("Server returned ${response.code}: ${response.body?.string()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun search(query: String, top: Int = 10): Result<SearchResponse> = withContext(Dispatchers.IO) {
        val apiUrl = prefs.apiUrl.trimEnd('/')
        val token = prefs.bearerToken
        if (apiUrl.isEmpty() || token.isEmpty()) {
            return@withContext Result.failure(
                IllegalStateException("API URL and bearer token must be configured in Settings")
            )
        }
        try {
            val request = Request.Builder()
                .url("$apiUrl/search?q=${URLEncoder.encode(query, "UTF-8")}&top=$top")
                .get()
                .header("Authorization", "Bearer $token")
                .build()
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                return@withContext Result.failure(IOException("Server returned ${response.code}"))
            }
            val json = JSONObject(response.body?.string() ?: "{}")
            val sections = json.optJSONObject("sections") ?: JSONObject()

            fun parseSection(key: String): List<SearchResult> {
                val arr = sections.optJSONArray(key) ?: return emptyList()
                return (0 until arr.length()).map { i ->
                    val item = arr.getJSONObject(i)
                    SearchResult(
                        url = item.optString("url"),
                        title = item.optString("title"),
                        distance = if (item.has("distance")) item.getDouble("distance") else null,
                        matchedTags = item.optJSONArray("matched_tags")
                            ?.let { tags -> (0 until tags.length()).map { tags.getString(it) } }
                            ?: emptyList()
                    )
                }
            }

            Result.success(
                SearchResponse(
                    query = json.optString("query"),
                    vector = parseSection("vector"),
                    title = parseSection("title"),
                    tags = parseSection("tags")
                )
            )
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}

fun decodeJsString(jsResult: String): String =
    try { JSONTokener(jsResult).nextValue() as? String ?: jsResult }
    catch (e: Exception) { jsResult }
