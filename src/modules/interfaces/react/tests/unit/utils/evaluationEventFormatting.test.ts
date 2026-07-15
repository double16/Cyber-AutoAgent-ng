import {describe, expect, it} from '@jest/globals';
import {formatAutoRunEvaluationEvent} from '../../../src/utils/evaluationEventFormatting.js';

describe('formatAutoRunEvaluationEvent', () => {
  it('labels every non-metric evaluation stage as preparation', () => {
    for (const kind of ['evaluation_data', 'reference_topics', 'rubric_judge', 'evaluation_policy']) {
      expect(formatAutoRunEvaluationEvent({
        type: 'progress_update',
        operation_stage: 'ragas_evaluation',
        evaluation_step_kind: kind,
        evaluation_step_label: `Operation: ${kind}`,
      })).toContain('Ragas evaluation preparation');
    }
  });

  it('formats indexed metrics and only user-relevant step outcomes', () => {
    expect(formatAutoRunEvaluationEvent({
      type: 'progress_update',
      operation_stage: 'ragas_evaluation',
      evaluation_step_kind: 'metric',
      evaluation_step_index: 2,
      evaluation_step_total: 5,
      evaluation_step_label: 'Operation: Goal Accuracy',
    })).toContain('Ragas evaluation 2/5: Operation: Goal Accuracy');
    expect(formatAutoRunEvaluationEvent({
      type: 'evaluation_step_complete',
      evaluation_scope: 'operation',
      evaluation_metric: 'goal_accuracy',
      status: 'failed',
      message: 'Metric evaluation failed',
    })).toContain('operation: goal accuracy failed: Metric evaluation failed');
    expect(formatAutoRunEvaluationEvent({
      type: 'evaluation_step_complete',
      status: 'completed',
    })).toBeNull();
  });

  it('formats finalized scores and negative completion outcomes', () => {
    expect(formatAutoRunEvaluationEvent({
      type: 'evaluation_complete',
      status: 'completed',
      metrics_evaluated: 2,
      average_score: 0.7,
      scores: {'operation/evidence_quality': 0.8, 'operation/goal_accuracy': 0.6},
    })).toBe(
      '✅ Evaluation complete: 2 metrics | Average 70.0% | '
      + 'operation/evidence_quality=80.0%, operation/goal_accuracy=60.0%'
    );
    expect(formatAutoRunEvaluationEvent({
      type: 'evaluation_complete',
      status: 'no_results',
      message: 'Evaluation produced no scores',
    })).toContain('Evaluation completed without results');
    expect(formatAutoRunEvaluationEvent({
      type: 'evaluation_complete',
      status: 'failed',
      message: 'Evaluation failed; see logs for details',
    })).toContain('Evaluation failed');
  });
});
