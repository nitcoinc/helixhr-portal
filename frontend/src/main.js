import './index.css'

import { createApp } from 'vue'
import router from './router'
import App from './App.vue'

import { setConfig, resourcesPlugin } from 'frappe-ui'
import { apiRequest } from './lib/api'

let app = createApp(App)

setConfig('resourceFetcher', apiRequest)

app.use(router)
app.use(resourcesPlugin)

app.mount('#app')
