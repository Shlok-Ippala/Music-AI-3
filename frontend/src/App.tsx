import React, { useState, useEffect, useCallback, useRef } from 'react';
import Header from './components/layout/Header';
import Sidebar from './components/layout/Sidebar';
import CenterPanel from './components/layout/CenterPanel';
import RightPanel from './components/layout/RightPanel';
import { clsx } from 'clsx';
import { getStatus, type ProjectStatus } from './services/api';
import { wsService, type WsEvent } from './services/websocket';
import type { MessageProps } from './components/chat/ChatBubble';

const DEFAULT_STATUS: ProjectStatus = {
  tempo: 120,
  time_sig: '4/4',
  track_count: 0,
  tracks: [],
  bridge_connected: false,
  playing: false,
  recording: false,
};

let msgCounter = 0;
function newId() { return `msg-${++msgCounter}`; }

function App() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isProMode, setIsProMode] = useState(false);
  const [project, setProject] = useState<ProjectStatus>(DEFAULT_STATUS);
  const [messages, setMessages] = useState<MessageProps[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const pendingAriaIdRef = useRef<string | null>(null);

  // Poll project status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const status = await getStatus();
        setProject(status);
      } catch {}
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, []);

  // WebSocket connection
  useEffect(() => {
    wsService.connect((event: WsEvent) => {
      switch (event.type) {
        case 'thinking':
          setIsThinking(true);
          const pendingId = newId();
          pendingAriaIdRef.current = pendingId;
          setMessages(prev => [...prev, {
            id: pendingId,
            type: 'aria',
            content: '',
            toolCalls: [],
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            isThinking: true,
          }]);
          break;

        case 'tool_call':
          setMessages(prev => prev.map(m =>
            m.id === pendingAriaIdRef.current
              ? { ...m, toolCalls: [...(m.toolCalls || []), { name: event.name!, args: event.args! }] }
              : m
          ));
          break;

        case 'message':
          setIsThinking(false);
          setMessages(prev => prev.map(m =>
            m.id === pendingAriaIdRef.current
              ? { ...m, content: event.content!, isThinking: false }
              : m
          ));
          pendingAriaIdRef.current = null;
          break;

        case 'project_update':
          if (event.data) setProject(event.data);
          break;

        case 'error':
          setIsThinking(false);
          setMessages(prev => prev.map(m =>
            m.id === pendingAriaIdRef.current
              ? { ...m, content: `Error: ${event.content}`, isThinking: false }
              : m
          ));
          pendingAriaIdRef.current = null;
          break;
      }
    });
    return () => wsService.disconnect();
  }, []);

  const sendMessage = useCallback((content: string) => {
    if (!content.trim()) return;
    setMessages(prev => [...prev, {
      id: newId(),
      type: 'user',
      content,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }]);
    wsService.send(content);
  }, []);

  return (
    <div className="flex h-screen w-full flex-col bg-bg-main text-text-primary relative z-0">
      <div className="noise-bg z-[-1]"></div>
      <Header
        isProMode={isProMode}
        setIsProMode={setIsProMode}
        toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
        bridgeConnected={project.bridge_connected}
      />

      <div className="flex flex-1 overflow-hidden relative z-10">
        <div className={clsx(
          "transition-all duration-300 ease-in-out border-r border-white/5 bg-bg-panel/50 backdrop-blur-sm",
          isSidebarOpen ? "w-[240px]" : "w-0 overflow-hidden border-none"
        )}>
          <Sidebar bridgeConnected={project.bridge_connected} />
        </div>

        <div className="flex-1 min-w-0 flex flex-col bg-bg-main relative">
          <CenterPanel
            isProMode={isProMode}
            project={project}
            onProjectChange={setProject}
            onSendMessage={sendMessage}
          />
          <div className="absolute bottom-0 left-0 right-0 h-16 pointer-events-none opacity-20 bg-gradient-to-t from-accent-cyan/10 to-transparent">
            <div className="w-full h-full border-t border-accent-cyan shrink-0 animate-pulse-slow"></div>
          </div>
        </div>

        <div className="w-[360px] border-l border-white/5 bg-bg-panelLight/40 backdrop-blur-md flex flex-col shrink-0">
          <RightPanel
            messages={messages}
            isThinking={isThinking}
            onSendMessage={sendMessage}
          />
        </div>
      </div>
    </div>
  );
}

export default App;
