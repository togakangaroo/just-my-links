package com.justmylinks.ui.screens

import android.annotation.SuppressLint
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.justmylinks.data.ApiClient
import com.justmylinks.data.PreferencesRepository
import com.justmylinks.data.decodeJsString
import kotlinx.coroutines.launch

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun BrowserScreen(targetUrl: String) {
    val context = LocalContext.current
    val prefs = remember { PreferencesRepository(context) }
    val apiClient = remember { ApiClient(prefs) }
    val scope = rememberCoroutineScope()

    var urlInput by remember { mutableStateOf(targetUrl) }
    var currentUrl by remember { mutableStateOf("") }
    var isSaving by remember { mutableStateOf(false) }
    var statusMessage by remember { mutableStateOf<Pair<Boolean, String>?>(null) } // isError, message
    val webViewRef = remember { mutableStateOf<WebView?>(null) }

    // Respond to externally-set URLs (share intent, search result tap)
    LaunchedEffect(targetUrl) {
        if (targetUrl.isNotEmpty()) {
            val url = normalizeUrl(targetUrl)
            urlInput = url
            webViewRef.value?.loadUrl(url)
        }
    }

    // WebView back navigation
    BackHandler(enabled = webViewRef.value?.canGoBack() == true) {
        webViewRef.value?.goBack()
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = urlInput,
                onValueChange = { urlInput = it; statusMessage = null },
                modifier = Modifier.weight(1f),
                singleLine = true,
                placeholder = { Text("Enter URL") },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Go
                ),
                keyboardActions = KeyboardActions(
                    onGo = { webViewRef.value?.loadUrl(normalizeUrl(urlInput)) }
                )
            )
            Spacer(modifier = Modifier.width(4.dp))
            IconButton(
                onClick = {
                    val wv = webViewRef.value ?: return@IconButton
                    val url = currentUrl.ifEmpty { return@IconButton }
                    isSaving = true
                    statusMessage = null
                    wv.evaluateJavascript("document.documentElement.outerHTML") { raw ->
                        val html = decodeJsString(raw)
                        scope.launch {
                            apiClient.saveDocument(url, html).fold(
                                onSuccess = { statusMessage = false to "Saved!" },
                                onFailure = { statusMessage = true to (it.message ?: "Error") }
                            )
                            isSaving = false
                        }
                    }
                },
                enabled = !isSaving && currentUrl.isNotEmpty()
            ) {
                if (isSaving) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                } else {
                    Icon(Icons.Default.Bookmark, contentDescription = "Save bookmark")
                }
            }
        }

        statusMessage?.let { (isError, msg) ->
            Text(
                text = msg,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
                style = MaterialTheme.typography.bodySmall,
                color = if (isError) MaterialTheme.colorScheme.error
                        else MaterialTheme.colorScheme.tertiary
            )
        }

        AndroidView(
            factory = { ctx ->
                WebView(ctx).also { wv ->
                    wv.settings.javaScriptEnabled = true
                    wv.settings.domStorageEnabled = true
                    wv.webViewClient = object : WebViewClient() {
                        override fun onPageFinished(view: WebView, url: String?) {
                            url?.let {
                                currentUrl = it
                                urlInput = it
                            }
                        }
                    }
                    webViewRef.value = wv
                    if (targetUrl.isNotEmpty()) wv.loadUrl(normalizeUrl(targetUrl))
                }
            },
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
        )
    }
}

private fun normalizeUrl(url: String): String =
    if (url.startsWith("http://") || url.startsWith("https://")) url else "https://$url"
