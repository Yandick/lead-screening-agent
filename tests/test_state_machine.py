"""状态机测试：plan 测试清单 3、4、5、6、11 的确定性部分。"""

from kapibala.schemas import Estimation, Intent
from kapibala.state_machine import ESCALATION_THRESHOLD, SessionState, StateMachine


def est(intent=Intent.INTERESTED, **kw) -> Estimation:
    return Estimation(intent=intent, **kw)


def test_two_consecutive_off_topic_escalates():
    sm = StateMachine()
    t1 = sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert not t1.escalated_now
    assert sm.get("c1").anomaly_count == 1
    t2 = sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert t2.escalated_now
    assert sm.get("c1").session is SessionState.ESCALATED


def test_two_consecutive_dissatisfied_escalates():
    sm = StateMachine()
    sm.apply("c1", est(dissatisfied=True))
    t = sm.apply("c1", est(dissatisfied=True))
    assert t.escalated_now
    assert sm.get("c1").session is SessionState.ESCALATED


def test_off_topic_and_dissatisfied_share_counter():
    """题目硬性约束：两者共用同一个计数器，混计达 2 次也要升级。"""
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    t = sm.apply("c1", est(intent=Intent.INTERESTED, dissatisfied=True))
    assert t.escalated_now


def test_normal_message_resets_counter():
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    sm.apply("c1", est(intent=Intent.NEEDS_INFO))  # 正常消息，计数器重置
    assert sm.get("c1").anomaly_count == 0
    t = sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert not t.escalated_now  # 重新从 1 计起
    assert sm.get("c1").session is SessionState.ACTIVE


def test_low_confidence_freezes_anomaly_counter():
    """低置信首次：异常计数器冻结（不增不清）。"""
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert sm.get("c1").anomaly_count == 1
    sm.apply("c1", est(confidence=0.3))
    assert sm.get("c1").anomaly_count == 1  # 冻结
    assert sm.get("c1").low_confidence_count == 1


def test_two_consecutive_low_confidence_escalates():
    sm = StateMachine()
    t1 = sm.apply("c1", est(confidence=0.3))
    assert not t1.escalated_now
    t2 = sm.apply("c1", est(confidence=0.2))
    assert t2.escalated_now
    assert sm.get("c1").session is SessionState.ESCALATED


def test_confident_message_resets_low_confidence_counter():
    sm = StateMachine()
    sm.apply("c1", est(confidence=0.3))
    sm.apply("c1", est(confidence=0.9))
    assert sm.get("c1").low_confidence_count == 0
    t = sm.apply("c1", est(confidence=0.3))
    assert not t.escalated_now


def test_rejected_closes_even_when_dissatisfied():
    """真值表规则 1：愤怒的拒绝者直接关闭，不安抚不升级。"""
    sm = StateMachine()
    t = sm.apply("c1", est(intent=Intent.REJECTED, dissatisfied=True))
    assert t.closed_now
    assert not t.escalated_now
    assert sm.get("c1").session is SessionState.CLOSED


def test_apply_is_noop_when_not_active():
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert sm.get("c1").session is SessionState.ESCALATED
    t = sm.apply("c1", est(intent=Intent.INTERESTED))
    assert not t.escalated_now and not t.closed_now
    assert sm.get("c1").session is SessionState.ESCALATED  # 不被消息恢复


def test_reactivate_restores_active_and_clears_counters():
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    sm.reactivate("c1")
    st = sm.get("c1")
    assert st.session is SessionState.ACTIVE
    assert st.anomaly_count == 0
    assert st.low_confidence_count == 0


def test_customer_states_are_isolated():
    sm = StateMachine()
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    sm.apply("c1", est(intent=Intent.OFF_TOPIC))
    assert sm.get("c1").session is SessionState.ESCALATED
    assert sm.get("c2").session is SessionState.ACTIVE
    assert sm.get("c2").anomaly_count == 0


def test_escalation_threshold_is_two():
    assert ESCALATION_THRESHOLD == 2
