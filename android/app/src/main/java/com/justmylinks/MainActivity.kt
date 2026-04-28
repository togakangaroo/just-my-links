package com.justmylinks

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.justmylinks.ui.screens.BrowserScreen
import com.justmylinks.ui.screens.SearchScreen
import com.justmylinks.ui.screens.SettingsScreen
import com.justmylinks.ui.theme.JustMyLinksTheme
import kotlinx.coroutines.flow.MutableStateFlow

class MainActivity : ComponentActivity() {

    private val targetUrlFlow = MutableStateFlow("")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        targetUrlFlow.value = extractSharedUrl(intent) ?: ""
        setContent {
            JustMyLinksTheme {
                App(
                    targetUrlFlow = targetUrlFlow,
                    startOnBrowser = targetUrlFlow.value.isNotEmpty()
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        extractSharedUrl(intent)?.let { targetUrlFlow.value = it }
    }

    private fun extractSharedUrl(intent: Intent?): String? {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            return intent.getStringExtra(Intent.EXTRA_TEXT)
        }
        return null
    }
}

sealed class Screen(val route: String, val label: String) {
    object Browser : Screen("browser", "Browser")
    object Search : Screen("search", "Search")
    object Settings : Screen("settings", "Settings")
}

private val bottomNavScreens = listOf(Screen.Browser, Screen.Search, Screen.Settings)

@Composable
fun App(targetUrlFlow: MutableStateFlow<String>, startOnBrowser: Boolean) {
    val navController = rememberNavController()
    var targetUrl by remember { mutableStateOf(targetUrlFlow.value) }

    // Collect new share intents while the app is open
    androidx.compose.runtime.LaunchedEffect(Unit) {
        targetUrlFlow.collect { url ->
            if (url.isNotEmpty()) {
                targetUrl = url
                navController.navigate(Screen.Browser.route) {
                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                    launchSingleTop = true
                    restoreState = true
                }
            }
        }
    }

    Scaffold(
        bottomBar = {
            val navBackStackEntry by navController.currentBackStackEntryAsState()
            val currentDestination = navBackStackEntry?.destination
            NavigationBar {
                bottomNavScreens.forEach { screen ->
                    NavigationBarItem(
                        icon = {
                            Icon(
                                when (screen) {
                                    Screen.Browser -> Icons.Default.Language
                                    Screen.Search -> Icons.Default.Search
                                    Screen.Settings -> Icons.Default.Settings
                                },
                                contentDescription = screen.label
                            )
                        },
                        label = { Text(screen.label) },
                        selected = currentDestination?.hierarchy?.any { it.route == screen.route } == true,
                        onClick = {
                            navController.navigate(screen.route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        }
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = if (startOnBrowser) Screen.Browser.route else Screen.Search.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(Screen.Browser.route) {
                BrowserScreen(targetUrl = targetUrl)
            }
            composable(Screen.Search.route) {
                SearchScreen(
                    onOpenUrl = { url ->
                        targetUrl = url
                        navController.navigate(Screen.Browser.route) {
                            popUpTo(navController.graph.findStartDestination().id) {
                                saveState = true
                            }
                            launchSingleTop = true
                            restoreState = true
                        }
                    }
                )
            }
            composable(Screen.Settings.route) {
                SettingsScreen()
            }
        }
    }
}
