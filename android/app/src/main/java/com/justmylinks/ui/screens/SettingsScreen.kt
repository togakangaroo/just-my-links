package com.justmylinks.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.justmylinks.data.PreferencesRepository

@Composable
fun SettingsScreen() {
    val context = LocalContext.current
    val prefs = remember { PreferencesRepository(context) }

    var apiUrl by remember { mutableStateOf(prefs.apiUrl) }
    var bearerToken by remember { mutableStateOf(prefs.bearerToken) }
    var saved by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall)

        OutlinedTextField(
            value = apiUrl,
            onValueChange = { apiUrl = it; saved = false },
            label = { Text("API URL") },
            placeholder = { Text("https://abc123.execute-api.us-east-1.amazonaws.com/dev") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
        )

        OutlinedTextField(
            value = bearerToken,
            onValueChange = { bearerToken = it; saved = false },
            label = { Text("Bearer Token") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password)
        )

        Button(
            onClick = {
                prefs.apiUrl = apiUrl
                prefs.bearerToken = bearerToken
                saved = true
            },
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Save")
        }

        if (saved) {
            Text(
                "Settings saved",
                color = MaterialTheme.colorScheme.tertiary,
                style = MaterialTheme.typography.bodySmall
            )
        }
    }
}
