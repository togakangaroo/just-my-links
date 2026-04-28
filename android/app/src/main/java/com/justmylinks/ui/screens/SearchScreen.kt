package com.justmylinks.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.justmylinks.data.ApiClient
import com.justmylinks.data.PreferencesRepository
import com.justmylinks.data.SearchResponse
import com.justmylinks.data.SearchResult
import kotlinx.coroutines.launch

@Composable
fun SearchScreen(onOpenUrl: (String) -> Unit) {
    val context = LocalContext.current
    val prefs = remember { PreferencesRepository(context) }
    val apiClient = remember { ApiClient(prefs) }
    val scope = rememberCoroutineScope()

    var query by remember { mutableStateOf("") }
    var isSearching by remember { mutableStateOf(false) }
    var results by remember { mutableStateOf<SearchResponse?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    fun doSearch() {
        if (query.isBlank()) return
        isSearching = true
        error = null
        scope.launch {
            apiClient.search(query).fold(
                onSuccess = { results = it },
                onFailure = { error = it.message }
            )
            isSearching = false
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Search bookmarks") },
            placeholder = { Text("query text #tag") },
            singleLine = true,
            trailingIcon = {
                IconButton(onClick = ::doSearch) {
                    Icon(Icons.Default.Search, contentDescription = "Search")
                }
            },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { doSearch() })
        )

        if (isSearching) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
        }

        error?.let {
            Text(
                text = it,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(vertical = 4.dp)
            )
        }

        results?.let { response ->
            LazyColumn(modifier = Modifier.padding(top = 8.dp)) {
                if (response.vector.isNotEmpty()) {
                    item { SectionHeader("Semantic") }
                    items(response.vector) { ResultItem(it, onOpenUrl) }
                }
                if (response.title.isNotEmpty()) {
                    item { SectionHeader("Title") }
                    items(response.title) { ResultItem(it, onOpenUrl) }
                }
                if (response.tags.isNotEmpty()) {
                    item { SectionHeader("Tags") }
                    items(response.tags) { ResultItem(it, onOpenUrl) }
                }
                if (response.vector.isEmpty() && response.title.isEmpty() && response.tags.isEmpty()) {
                    item {
                        Text(
                            "No results",
                            modifier = Modifier.padding(vertical = 16.dp),
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(top = 12.dp, bottom = 4.dp)
    )
}

@Composable
private fun ResultItem(result: SearchResult, onOpenUrl: (String) -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp)
            .clickable { onOpenUrl(result.url) }
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            if (result.title.isNotEmpty()) {
                Text(
                    result.title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium
                )
            }
            Text(
                result.url,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            if (result.matchedTags.isNotEmpty()) {
                Text(
                    result.matchedTags.joinToString(" ") { "#$it" },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.secondary
                )
            }
        }
    }
}
