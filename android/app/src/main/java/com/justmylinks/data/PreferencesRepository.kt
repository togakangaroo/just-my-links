package com.justmylinks.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

class PreferencesRepository(context: Context) {

    private val plainPrefs: SharedPreferences =
        context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    private val encryptedPrefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "secure_settings",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    var apiUrl: String
        get() = plainPrefs.getString("api_url", "") ?: ""
        set(value) { plainPrefs.edit().putString("api_url", value).apply() }

    var bearerToken: String
        get() = encryptedPrefs.getString("bearer_token", "") ?: ""
        set(value) { encryptedPrefs.edit().putString("bearer_token", value).apply() }
}
