import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

const MobileHUD = () => {
  const [state, setState] = useState<any>(null);
  const [notifications, setNotifications] = useState<any[]>([]);

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8001/ws/mobile');
    
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'state_update') {
        setState(msg.data);
      } else if (msg.type === 'notification') {
        setNotifications(prev => [...prev, msg.data]);
        setTimeout(() => {
          setNotifications(prev => prev.slice(1));
        }, 5000);
      }
    };

    return () => ws.close();
  }, []);

  if (!state) return (
    <div className="min-h-screen bg-black flex items-center justify-center text-cyan-500 font-mono">
      <div className="animate-pulse text-2xl">SCANNING FOR AGENTX...</div>
    </div>
  );

  return (
    <div className="min-h-screen bg-black text-cyan-500 font-mono p-4 flex flex-col gap-4">
      {/* Header */}
      <div className="border-b border-cyan-900 pb-2 flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold tracking-tighter">AGENTX HUD</h1>
          <div className="text-xs text-cyan-800">MOBILE BRIDGE ACTIVE</div>
        </div>
        <div className="text-right">
          <div className={`text-xs ${state.is_healthy ? 'text-green-500' : 'text-red-500'}`}>
            {state.is_healthy ? '● SYSTEM HEALTHY' : '● CRITICAL WARN'}
          </div>
          <div className="text-[10px] text-cyan-900">{new Date().toLocaleTimeString()}</div>
        </div>
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 gap-2">
        <div className="border border-cyan-900 p-3 bg-cyan-950/20 backdrop-blur">
          <div className="text-[10px] text-cyan-700">ACTIVE MISSIONS</div>
          <div className="text-3xl font-bold text-white">{state.active_tasks || 0}</div>
        </div>
        <div className="border border-cyan-900 p-3 bg-cyan-950/20 backdrop-blur">
          <div className="text-[10px] text-cyan-700">SWARM MODE</div>
          <div className="text-xl font-bold text-white truncate">{state.operating_mode || 'NORMAL'}</div>
        </div>
      </div>

      {/* Mission Control */}
      <div className="flex-1 flex flex-col gap-2 overflow-hidden">
        <div className="text-[10px] text-cyan-700 mt-2">REAL-TIME TELEMETRY</div>
        <div className="flex-1 border border-cyan-900 bg-cyan-950/10 p-2 overflow-y-auto text-xs space-y-1">
          {state.logs?.map((log: any, i: number) => (
            <div key={i} className="flex gap-2">
              <span className="text-cyan-800">[{log.time}]</span>
              <span className={log.level === 'error' ? 'text-red-400' : 'text-cyan-400'}>
                {log.message}
              </span>
            </div>
          )) || <div className="text-cyan-900">NO LIVE DATA...</div>}
        </div>
      </div>

      {/* Quick Action Dock */}
      <div className="grid grid-cols-3 gap-2 pb-safe">
        <button className="border border-cyan-700 p-4 bg-cyan-900/30 text-white font-bold active:bg-cyan-500 active:text-black transition-colors">
          RUN
        </button>
        <button className="border border-yellow-700 p-4 bg-yellow-900/30 text-white font-bold active:bg-yellow-500 active:text-black transition-colors">
          HOLD
        </button>
        <button className="border border-red-700 p-4 bg-red-900/30 text-white font-bold active:bg-red-500 active:text-black transition-colors">
          KILL
        </button>
      </div>

      {/* Notifications Toast */}
      <div className="fixed top-4 left-4 right-4 pointer-events-none space-y-2">
        <AnimatePresence>
          {notifications.map((n, i) => (
            <motion.div
              key={i}
              initial={{ x: 100, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ opacity: 0 }}
              className="bg-cyan-500 text-black p-3 border-l-4 border-white shadow-lg pointer-events-auto"
            >
              <div className="font-bold text-xs uppercase">{n.title}</div>
              <div className="text-sm">{n.body}</div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default MobileHUD;
