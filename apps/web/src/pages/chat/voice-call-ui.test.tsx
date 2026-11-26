// @vitest-environment jsdom
/* 语音通话 UI 测试:入口位置(空草稿时替代发送键)与通话浮层(小球态/静音/失败安静提示)。 */

import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { ComposerBar } from "./composer-bar";
import { ChatThread } from "./chat-thread";
import { useVoiceCallPage } from "./use-voice-call-page";
import { VoiceCallBar } from "./voice-call-bar";

function makeComposerProps(
  over: Partial<React.ComponentProps<typeof ComposerBar>> = {},
): React.ComponentProps<typeof ComposerBar> {
  return {
    sessionId: undefined,
    canGenerateReport: false,
    streaming: false,
    uploading: false,
    recording: false,
    transcribing: false,
    agentMode: "recipe",
    setAgentMode: vi.fn(),
    draft: "",
    setDraft: vi.fn(),
    attachments: [],
    fileRef: createRef<HTMLInputElement>(),
    report: {
      open: false,
      setOpen: vi.fn(),
      instruction: "",
      setInstruction: vi.fn(),
      title: "",
      setTitle: vi.fn(),
      busy: false,
      error: null,
      submit: vi.fn().mockResolvedValue(undefined),
    },
    onPickFile: vi.fn().mockResolvedValue(undefined),
    onMicClick: vi.fn().mockResolvedValue(undefined),
    onRemoveAttachment: vi.fn(),
    onSubmit: vi.fn(),
    onStop: vi.fn(),
    voiceEntry: (
      <button type="button" aria-label="语音通话">
        voice
      </button>
    ),
    ...over,
  };
}

describe("ComposerBar 语音通话入口位置", () => {
  it("非流式且输入框为空时,语音通话键替代发送键", () => {
    render(<ComposerBar {...makeComposerProps({ draft: "   ", streaming: false })} />);
    expect(screen.getByRole("button", { name: "语音通话" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
  });

  it("输入非空文本后切回发送键;流式期间切为取消本轮", () => {
    const { rerender } = render(
      <ComposerBar {...makeComposerProps({ draft: "上个月销售额", streaming: false })} />,
    );
    expect(screen.getByRole("button", { name: "发送" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "语音通话" })).not.toBeInTheDocument();

    rerender(<ComposerBar {...makeComposerProps({ draft: "", streaming: true })} />);
    expect(screen.getByRole("button", { name: "取消本轮问答" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "语音通话" })).not.toBeInTheDocument();
  });
});

describe("VoiceCallBar 通话浮层与失败提示", () => {
  it("空闲态不渲染;通话态展示状态文案与挂断键", async () => {
    const onHangUp = vi.fn();
    const { rerender } = render(
      <VoiceCallBar phase="idle" muted={false} failure={null} onHangUp={onHangUp} />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(
      <VoiceCallBar
        phase="listening"
        muted={false}
        failure={null}
        sampleAudioLevels={() => ({ user: 0.4, model: 0 })}
        onHangUp={onHangUp}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("聆听中,请说出你的问题");
    await userEvent.click(screen.getByRole("button", { name: "挂断" }));
    expect(onHangUp).toHaveBeenCalledTimes(1);

    rerender(<VoiceCallBar phase="querying" muted={false} failure={null} onHangUp={onHangUp} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在为您查询…");

    rerender(<VoiceCallBar phase="speaking" muted={true} failure={null} onHangUp={onHangUp} />);
    expect(screen.getByRole("status")).toHaveTextContent("已静音");
  });

  it("失败态展示安静提示条并支持关闭", async () => {
    const onHangUp = vi.fn();
    render(<VoiceCallBar phase="failed" muted={false} failure="ice" onHangUp={onHangUp} />);
    expect(screen.getByRole("alert")).toHaveTextContent("当前网络不支持语音通话");
    await userEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onHangUp).toHaveBeenCalledTimes(1);
  });
});

describe("useVoiceCallPage 闭包新鲜度与卸载清理", () => {
  it("通话中 streaming 从 false 变为 true 后,新 turn.request 仍能触发最新 stopStreaming", async () => {
    let capturedOnTurn: ((turn: { token: string; question: string }) => void) | null = null;
    const stopTrack = vi.fn();
    const pcClose = vi.fn();
    const wsClose = vi.fn();
    const stopStreaming = vi.fn();
    const submitTurn = vi.fn();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        let url: string;
        if (typeof input === "string") url = input;
        else url = input instanceof URL ? input.href : input.url;
        if (url.endsWith("/voice/calls/capability")) {
          return Promise.resolve(new Response(JSON.stringify({ enabled: true }), { status: 200 }));
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({ answer: "v=0\r\nok\r\n", ticket: "tk-1", call_id: "c-1" }),
            { status: 200 },
          ),
        );
      }),
    );
    const micTrack = { stop: stopTrack };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [micTrack] }),
      },
    });
    const replaceTrack = vi.fn().mockResolvedValue(undefined);
    const addTransceiver = vi.fn(() => ({ sender: { replaceTrack } }));
    let pushDcMessage: ((data: string) => void) | null = null;
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "new";
        iceConnectionState = "new";
        localDescription = { sdp: "v=0\r\n" };
        addTrack = vi.fn();
        addTransceiver = addTransceiver;
        createDataChannel = vi.fn(() => {
          const dc = {
            readyState: "open",
            send: vi.fn(),
            close: vi.fn(),
            onmessage: null as ((ev: { data: string }) => void) | null,
          };
          pushDcMessage = (data: string) => {
            dc.onmessage?.({ data });
          };
          return dc;
        });
        createOffer = vi.fn().mockResolvedValue({ type: "offer", sdp: "v=0\r\n" });
        setLocalDescription = vi.fn().mockResolvedValue(undefined);
        setRemoteDescription = vi.fn().mockResolvedValue(undefined);
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        close = pcClose;
      },
    );
    let pushWsMessage: ((data: string) => void) | null = null;
    vi.stubGlobal(
      "WebSocket",
      class {
        static readonly OPEN = 1;
        readyState = 1;
        onmessage: ((ev: { data: string }) => void) | null = null;
        onclose: (() => void) | null = null;
        send = vi.fn();
        close = wsClose;
        constructor() {
          pushWsMessage = (data: string) => {
            this.onmessage?.({ data });
          };
        }
      },
    );

    function Harness({ streaming }: { streaming: boolean }) {
      const voice = useVoiceCallPage({
        tenantId: "default",
        submitTurn,
        stopStreaming,
        streaming,
      });
      capturedOnTurn = () => {
        pushWsMessage?.(
          JSON.stringify({
            type: "turn.request",
            token: "vt-2",
            question: "第二问",
          }),
        );
      };
      return (
        <button type="button" onClick={voice.start}>
          start
        </button>
      );
    }

    const { rerender, unmount } = render(<Harness streaming={false} />);
    await userEvent.click(screen.getByRole("button", { name: "start" }));
    await waitFor(() => expect(pushWsMessage).not.toBeNull());
    // 建连阶段先用空 sendrecv transceiver 协商,未收到 session.updated 前不发送麦克风流
    expect(addTransceiver).toHaveBeenCalledWith("audio", { direction: "sendrecv" });
    expect(replaceTrack).not.toHaveBeenCalled();
    act(() => {
      pushDcMessage?.(JSON.stringify({ type: "session.updated" }));
    });
    expect(replaceTrack).toHaveBeenCalledWith(micTrack);
    // 通话建好后流式状态切为 true
    rerender(<Harness streaming={true} />);
    act(() => {
      capturedOnTurn?.({ token: "vt-2", question: "第二问" });
    });
    expect(stopStreaming).toHaveBeenCalledWith({ restoreDraft: false });
    expect(submitTurn).toHaveBeenCalledWith("第二问", "vt-2");

    // 停止工具到达:后端已取消在途轮,前端不得再发第二次取消,也不得挂断通话
    act(() => {
      pushWsMessage?.(JSON.stringify({ type: "turn.stopped" }));
    });
    expect(stopStreaming).toHaveBeenCalledTimes(1);
    expect(wsClose).not.toHaveBeenCalled();

    // 卸载时自动释放麦克风与连接
    unmount();
    expect(stopTrack).toHaveBeenCalled();
    expect(pcClose).toHaveBeenCalled();
    expect(wsClose).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });
});

describe("useVoiceCallPage 建立失败清理", () => {
  it("牵线成功后建连失败会主动 DELETE 通话,不留下服务端幽灵占用", async () => {
    const urlOf = (input: RequestInfo | URL): string => {
      if (typeof input === "string") return input;
      if (input instanceof URL) return input.href;
      return input.url;
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = urlOf(input);
      if (url.endsWith("/voice/calls/capability")) {
        return Promise.resolve(new Response(JSON.stringify({ enabled: true }), { status: 200 }));
      }
      if (url.includes("/voice/calls/c-1") && init?.method === "DELETE") {
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }
      return Promise.resolve(
        new Response(JSON.stringify({ answer: "v=0\r\nok\r\n", ticket: "tk-1", call_id: "c-1" }), {
          status: 200,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const micTrack = { stop: vi.fn() };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [micTrack] }) },
    });
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        iceGatheringState = "complete";
        connectionState = "new";
        iceConnectionState = "new";
        localDescription = { sdp: "v=0\r\n" };
        addTransceiver = vi.fn(() => ({ sender: { replaceTrack: vi.fn() } }));
        createDataChannel = vi.fn(() => ({
          readyState: "open",
          send: vi.fn(),
          close: vi.fn(),
          onmessage: null,
        }));
        createOffer = vi.fn().mockResolvedValue({ type: "offer", sdp: "v=0\r\n" });
        setLocalDescription = vi.fn().mockResolvedValue(undefined);
        setRemoteDescription = vi.fn().mockRejectedValue(new Error("bad answer"));
        addEventListener = vi.fn();
        removeEventListener = vi.fn();
        close = vi.fn();
      },
    );

    const { result, unmount } = renderHook(() =>
      useVoiceCallPage({
        tenantId: "default",
        submitTurn: vi.fn(),
        stopStreaming: vi.fn(),
        streaming: false,
      }),
    );
    await act(async () => {
      result.current.start();
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) => urlOf(input).endsWith("/voice/calls/c-1") && init?.method === "DELETE",
        ),
      ).toBe(true),
    );
    expect(result.current.failure).toBe("network");
    unmount();
    vi.unstubAllGlobals();
  });
});

describe("ChatThread 在途轮切换重建", () => {
  it("在途轮被新问同帧顶替时,ChatThread 凭 streaming.id 重建 StreamingTurn 并发起新请求", async () => {
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise<Response>(() => {
          // 保持流悬空,验证两轮各自发起独立请求
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const makeTurn = (id: number, question: string) => ({
      id,
      question,
      body: { question, datasource_key: "sakila", voice_turn_token: `vt-${id}` },
      attSnapshot: [],
      ac: new AbortController(),
    });
    const baseProps = {
      sessionId: undefined,
      messagesLoading: false,
      messages: undefined,
      suggestions: { items: [], loading: false },
      finalized: null,
      finalizedMsg: null,
      pendingConfirm: null,
      stoppedNotice: null,
      askError: null,
      onAsk: vi.fn(),
      onTraceId: vi.fn(),
      onResult: vi.fn(),
      onError: vi.fn(),
      onAllResolved: vi.fn(),
    };
    const { rerender, unmount } = render(
      <ChatThread {...baseProps} streaming={makeTurn(1, "第一问")} />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // 同帧从 turn1 切至 turn2(streaming 未经历 null),旧卡应卸载并立即触发第二轮请求
    rerender(<ChatThread {...baseProps} streaming={makeTurn(2, "第二问")} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText("第二问")).toBeInTheDocument();
    expect(screen.queryByText("第一问")).not.toBeInTheDocument();

    unmount();
    vi.unstubAllGlobals();
  });
});
