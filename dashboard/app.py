"""Run: python -m streamlit run dashboard/app.py (explicit runtime factory)."""
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st


def render(runtime):
    st.title('V1 Runtime Review')
    st.write('任务状态：' + runtime.status)
    st.write('阶段：COLLECTING → NORMALIZING → READY_FOR_REVIEW → ARCHIVED')
    st.metric('DATA_STATUS', runtime.data_status)
    st.metric('Evidence 数量', len(runtime.prompt.evidence_ids) if runtime.prompt else 0)
    actor = st.text_input('确认人', key='actor')
    identifier = st.text_input('Evidence ID', key='evidence_id')
    if st.button('准备分析', disabled=runtime.flow is not None):
        runtime.prepare(identifier)
        st.rerun()
    if runtime.prompt is None:
        st.info('等待已归档的 canonical Evidence。')
        return
    st.json(runtime.indicators)
    st.code(runtime.prompt.system_prompt + '\n' + runtime.prompt.user_prompt, language='text')
    if runtime.data_status == 'PARTIAL':
        st.warning('数据不完整；缺失或未验证内容保持原状态。')
    if st.button('生成报告', disabled=not actor.strip() or runtime.executor is None or runtime.flow is not None):
        runtime.generate(actor_id=actor)
        st.rerun()
    if runtime.executor is None:
        st.info('当前未配置模型执行器；可查看指标与 Prompt。')
    if runtime.flow is not None:
        st.json(runtime.flow.draft.to_dict())
        if runtime.prepared is None:
            reason = st.text_input('驳回原因', key='reason')
            for item in runtime.flow.items:
                if item.target == 'FINAL_REPORT':
                    continue
                state = runtime.flow.current(*item.key).value
                st.write(item.target + ' / ' + item.reason_code + ' / ' + state)
                st.json(item.to_dict())
                if st.button('人工确认 ' + item.target, key='confirm:' + ':'.join(item.key),
                             disabled=state != 'PENDING' or not actor.strip()):
                    runtime.decide(*item.key, actor_id=actor)
                    st.rerun()
                if st.button('驳回 ' + item.target, key='reject:' + ':'.join(item.key),
                             disabled=state != 'PENDING' or not actor.strip() or not reason.strip()):
                    runtime.decide(*item.key, actor_id=actor, reject=True, reason=reason)
                    st.rerun()
            ready = all(runtime.flow.current(*i.key).value != 'PENDING'
                        for i in runtime.flow.items if i.target != 'FINAL_REPORT')
            if st.button('准备归档快照', disabled=not ready):
                runtime.prepare_archive()
                st.rerun()
        else:
            st.json(runtime.prepared.to_dict())
            if st.button('人工确认并归档', disabled=not actor.strip() or runtime.final is not None):
                runtime.confirm_and_archive(actor_id=actor)
                st.rerun()
    if runtime.final is not None:
        st.success('ARCHIVED')
        st.json(runtime.final.snapshot.to_dict())


def main():
    if 'runtime' not in st.session_state:
        factory = os.environ.get('V1_RUNTIME_FACTORY')
        if not factory:
            st.title('V1 Runtime Review')
            st.write('任务状态：COLLECTING')
            st.metric('DATA_STATUS', 'PARTIAL')
            st.metric('Evidence 数量', 0)
            st.info('尚未选择运行数据。请先配置本地运行入口。')
            st.button('人工确认并归档', disabled=True)
            return
        try:
            module, function = factory.split(':', 1)
            st.session_state.runtime = getattr(importlib.import_module(module), function)()
        except (ValueError, OSError, KeyError, ImportError):
            st.error('运行数据配置不可用。')
            return
    try:
        render(st.session_state.runtime)
    except (ValueError, OSError):
        # Never display raw responses/credentials in exceptions.
        st.error('操作未完成；请检查数据完整性或待确认事项。')


if __name__ == '__main__':
    main()
