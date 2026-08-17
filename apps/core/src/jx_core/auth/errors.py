"""Stable, user-readable authentication errors and their HTTP mapping."""

from __future__ import annotations

from typing import Any

ERROR_DEFINITIONS: dict[str, tuple[int, str]] = {
    "validation_error": (422, "提交的信息不完整或格式不正确"),
    "platform_terms_outdated": (409, "平台条款已更新，请刷新后重新确认"),
    "username_taken": (409, "该用户名已被使用"),
    "invalid_credentials": (401, "用户名或密码错误"),
    "login_temporarily_locked": (423, "尝试次数过多，请 15 分钟后再试"),
    "account_disabled": (403, "账号已停用，请联系管理员"),
    "not_authenticated": (401, "请先登录"),
    "session_expired": (401, "登录状态已失效，请重新登录"),
    "password_change_required": (403, "请先修改临时密码"),
    "current_password_incorrect": (400, "当前密码不正确"),
    "new_password_must_differ": (422, "新密码不能与当前密码相同"),
    "forbidden": (403, "没有执行此操作的权限"),
    "user_not_found": (404, "用户不存在"),
    "csrf_origin_rejected": (403, "请求来源未通过安全校验"),
    "avatar_too_large": (413, "头像文件不能超过 2 MB"),
    "avatar_type_invalid": (422, "头像只支持 JPEG、PNG 或 WebP 图片"),
    "avatar_decode_failed": (422, "头像图片无法安全解析"),
    "avatar_unavailable": (404, "头像暂时不可用"),
    "internal_server_error": (500, "服务暂时不可用，请稍后重试"),
    "host_voice_already_configured": (409, "平台已经配置了启用中的主持音色"),
    "model_profile_unavailable": (409, "模型配置不存在或已停用"),
    "agent_name_taken": (409, "Agent 名称已存在，请换一个名称"),
    "agent_in_use": (409, "该 Agent 正被进行中的比赛使用，暂时不能修改"),
    "catalog_kind_invalid": (422, "配置类型无法识别"),
    "catalog_item_not_found": (404, "配置不存在"),
    "model_encryption_not_configured": (503, "模型密钥加密尚未配置，请联系管理员"),
    "llm_capacity_full": (503, "模型并发已满，比赛已暂停"),
    "llm_first_token_timeout": (503, "Agent 响应启动超时，比赛已暂停"),
    "llm_stream_stalled": (503, "Agent 文本生成中断，比赛已暂停"),
    "llm_provider_failed": (503, "Agent 文本生成失败，比赛已暂停"),
    "agent_decision_invalid": (503, "Agent 发言决策格式无效，比赛已暂停"),
    "tts_not_configured": (503, "实时语音合成尚未配置，请联系管理员"),
    "tts_start_timeout": (503, "Agent 语音合成启动超时，比赛已暂停"),
    "tts_stream_stalled": (503, "Agent 语音合成中断，比赛已暂停"),
    "tts_provider_failed": (503, "Agent 语音合成失败，比赛已暂停"),
    "tts_decode_failed": (503, "Agent 音频处理失败，比赛已暂停"),
    "voice_profile_unavailable": (409, "Agent 音色不存在、类型不匹配或已停用"),
    "host_voice_unavailable": (409, "主持音色不存在或已停用"),
    "rule_invalid": (422, "赛制阶段或动作配置不合法"),
    "rule_not_found": (404, "赛制不存在"),
    "rule_audio_not_ready": (409, "主持音频尚未全部生成并试听确认"),
    "rule_not_ready": (409, "赛制尚未完成音频确认，不能启用"),
    "rule_not_enabled": (409, "赛制当前未启用"),
    "rule_in_use": (409, "该赛制已被房间或比赛引用，不能删除；可以停用或编辑为新版本"),
    "human_participation_terms_outdated": (409, "请先确认最新的人类辩手参赛说明"),
    "rule_unavailable": (409, "赛制不存在、未启用或主持音频未就绪"),
    "topic_unavailable": (409, "辩题不存在或已停用"),
    "agent_unavailable": (409, "Agent 不存在、已停用或其模型/音色不可用"),
    "agent_capacity_insufficient": (409, "可用 Agent 数量不足，请启用更多 Agent 或选择更小赛制"),
    "agent_duplicate_in_room": (409, "同一个 Agent 不能在同一场比赛中占用多个席位"),
    "room_unavailable": (409, "房间不存在或当前不能加入"),
    "room_locked": (409, "比赛已开始，不能修改席位"),
    "room_member_required": (403, "请先加入房间"),
    "user_active_room_conflict": (409, "你已经参与其他未结束房间"),
    "room_owner_conflict": (409, "你已经创建其他未结束房间"),
    "spectator_capacity_full": (409, "观战席已满"),
    "seat_unavailable": (409, "该席位不可用或已被占用"),
    "room_seats_incomplete": (409, "请先为所有席位安排人类或 Agent"),
    "room_debater_unseated": (409, "仍有辩手未选择席位，请先选席或切换为观众"),
    "device_check_failed": (422, "设备检测未通过，请完成检测后重试"),
    "device_check_required": (409, "请先完成有效的设备检测"),
    "match_capacity_full": (409, "当前比赛容量已满，请稍后重试"),
    "disk_capacity_full": (507, "服务器磁盘空间不足，暂时不能开始新比赛"),
    "storage_unavailable": (503, "比赛文件存储暂时不可用，请联系管理员"),
    "room_code_collision": (409, "房间号生成冲突，请重试"),
    "room_code_invalid": (422, "请输入 6 位数字房间号"),
    "room_code_not_found": (404, "房间号不存在，请检查后重试"),
    "seat_human_occupied": (409, "该席位已被其他人占用，请选择其他席位"),
    "seat_swap_pending": (409, "你或对方已有待处理的交换申请"),
    "seat_swap_not_found": (404, "交换申请不存在或已失效"),
    "seat_swap_forbidden": (403, "当前不能申请或处理该交换"),
    "seat_swap_stale": (409, "席位状态已变化，请重新发起申请"),
    "livekit_not_configured": (503, "实时音频服务尚未配置，请联系管理员"),
    "host_audio_unavailable": (409, "主持音频暂时不可用，请刷新后重试"),
    "match_not_found": (404, "比赛不存在"),
    "admin_call_not_found": (404, "外部调用记录不存在"),
    "match_not_running": (409, "比赛当前不在运行状态"),
    "match_state_conflict": (409, "比赛状态已变化，请刷新后重试"),
    "match_command_unknown": (422, "比赛指令无法识别"),
    "match_actor_busy": (429, "比赛控制繁忙，请稍后重试"),
    "match_connection_stale": (409, "当前连接已失效，请刷新比赛页面"),
    "free_debate_not_implemented": (422, "自由辩论将在后续版本开放"),
    "free_debate_participants_required": (409, "自由辩论双方都需要至少一名辩手"),
    "hand_window_closed": (409, "当前不在申请发言时间内"),
    "hand_not_eligible": (403, "当前不是你所在阵营申请发言的时机"),
    "hand_already_raised": (409, "你已经申请发言"),
    "hand_not_raised": (409, "你当前没有申请发言"),
    "human_speaker_required": (409, "当前线性阶段需要先安排人类辩手"),
    "speech_not_finalized": (409, "发言文字尚未完成最终识别"),
    "speech_edit_forbidden": (403, "只能修改自己的已完成发言"),
    "transcript_archived": (409, "文字已归档，普通用户不能继续修改"),
    "transcript_text_invalid": (422, "文字记录长度不符合要求"),
    "match_file_not_found": (404, "比赛文件不存在"),
    "match_file_unavailable": (409, "比赛音频仍在处理中或暂不可用"),
    "asr_not_configured": (503, "实时语音识别服务尚未配置，请联系管理员"),
    "asr_start_timeout": (503, "语音识别启动超时，请重试"),
    "asr_final_timeout": (503, "语音识别最终结果超时，请重试"),
    "asr_task_failed": (503, "语音识别任务失败，请重试"),
    "asr_stream_failed": (503, "语音识别音频流中断，请重试"),
    "asr_audio_timeout": (503, "未收到可识别的实时音频，请检查麦克风后重试"),
    "judge_unavailable": (503, "AI 裁判暂时不可用，请稍后重试"),
    "judge_result_invalid": (422, "裁判分数或获胜方格式不符合规则"),
    "user_delete_forbidden": (409, "不能删除当前管理员账号"),
    "user_has_history": (409, "该用户已有房间或比赛历史，只能停用"),
    "match_delete_forbidden": (409, "只能删除已经结束或终止的比赛"),
    "match_delete_processing": (409, "比赛仍有赛后任务处理中，请稍后再删除"),
}


class AuthError(Exception):
    def __init__(self, code: str, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field_errors = field_errors


class APIError(Exception):
    """Transport-level error converted by the FastAPI exception handler."""

    def __init__(self, code: str, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field_errors = field_errors


def error_message(code: str) -> str:
    return ERROR_DEFINITIONS.get(code, ERROR_DEFINITIONS["internal_server_error"])[1]


def error_status(code: str) -> int:
    return ERROR_DEFINITIONS.get(code, ERROR_DEFINITIONS["internal_server_error"])[0]


def error_payload(
    code: str,
    request_id: str,
    field_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": error_message(code),
        "request_id": request_id,
    }
    if field_errors:
        error["field_errors"] = field_errors
    return {"error": error}


__all__ = [
    "APIError",
    "AuthError",
    "error_message",
    "error_payload",
    "error_status",
]
