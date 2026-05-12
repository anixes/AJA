import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';

export const useWebSocket = (url: string) => {
  const socketRef = useRef<WebSocket | null>(null);
  const setConnectionState = useStore((state) => state.setConnectionState);
  const setStats = useStore((state) => state.setStats);

  useEffect(() => {
    const connect = () => {
      console.log('[WS] Connecting to', url);
      const socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        console.log('[WS] Connected');
        setConnectionState('live');
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'state_update') {
            // Map system state to dashboard stats
            const state = message.data;
            setStats({
              activeAgents: state.active_agents || 0,
              batonCount: state.batons?.length || 0,
              safetyAlerts: state.alerts?.length || 0
            });
          }
        } catch (err) {
          console.error('[WS] Error parsing message:', err);
        }
      };

      socket.onclose = () => {
        console.log('[WS] Disconnected, retrying in 3s...');
        setConnectionState('offline');
        setTimeout(connect, 3000);
      };

      socket.onerror = (err) => {
        console.error('[WS] Error:', err);
        socket.close();
      };
    };

    connect();

    return () => {
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [url, setConnectionState, setStats]);

  return socketRef.current;
};
