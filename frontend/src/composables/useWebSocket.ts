import { ref, onUnmounted } from 'vue'

export function useWebSocket() {
  const connected = ref(false)
  let ws: WebSocket | null = null
  const listeners: Record<string, Array<(data: any) => void>> = {}

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${protocol}//${location.host}/ws`)

    ws.onopen = () => {
      connected.value = true
    }

    ws.onclose = () => {
      connected.value = false
      // Reconnect after 3 seconds
      setTimeout(connect, 3000)
    }

    ws.onmessage = (event) => {
      try {
        const { event: eventName, data } = JSON.parse(event.data)
        const callbacks = listeners[eventName]
        if (callbacks) {
          callbacks.forEach(cb => cb(data))
        }
      } catch {}
    }
  }

  function on(event: string, callback: (data: any) => void) {
    if (!listeners[event]) listeners[event] = []
    listeners[event].push(callback)
  }

  connect()

  onUnmounted(() => {
    if (ws) ws.close()
  })

  return { connected, on }
}
