import 'dart:convert';
import 'package:flutter/material.dart';

typedef LifeMutation = Future<bool> Function(
    String path, Map<String, dynamic> body);

// Wait for the closing transition as well as the result before disposing fields.
Future<void> showEditor({
  required BuildContext context,
  required WidgetBuilder builder,
}) async {
  final route = DialogRoute<void>(context: context, builder: builder);
  await Navigator.of(context, rootNavigator: true).push(route);
  await route.completed;
}

/// Reuses the parent API client and customer state; never calls the model.
class LifePanels extends StatefulWidget {
  const LifePanels({super.key, required this.data, required this.mutate});
  final Map<String, dynamic> data;
  final LifeMutation mutate;
  @override
  State<LifePanels> createState() => _LifePanelsState();
}

class _LifePanelsState extends State<LifePanels>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;
  bool get blocked => widget.data['blocked'] == true;
  Map<String, dynamic> get prefs =>
      Map<String, dynamic>.from(widget.data['preferences'] as Map);
  String amount(dynamic value, [String currency = 'EUR']) =>
      value == null ? 'Not specified' : '$value $currency';

  Future<void> inputDialog(
      String title,
      String note,
      Map<String, String> labels,
      Map<String, String> initial,
      Future<bool> Function(Map<String, String>) submit) async {
    final fields = {
      for (final key in labels.keys)
        key: TextEditingController(text: initial[key] ?? '')
    };
    var saving = false;
    String? error;
    await showEditor(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: Text(title),
                  content: SizedBox(
                      width: 440,
                      child: SingleChildScrollView(
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                        Text(note),
                        for (final e in fields.entries)
                          TextField(
                              controller: e.value,
                              decoration:
                                  InputDecoration(labelText: labels[e.key])),
                        if (error != null)
                          Text(error!,
                              style: const TextStyle(color: Colors.red)),
                      ]))),
                  actions: [
                    TextButton(
                        onPressed: saving ? null : () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: saving
                            ? null
                            : () async {
                                update(() => saving = true);
                                final ok = await submit({
                                  for (final e in fields.entries)
                                    e.key: e.value.text.trim()
                                });
                                if (!context.mounted) return;
                                if (ok) {
                                  Navigator.pop(context);
                                } else {
                                  update(() {
                                    saving = false;
                                    error =
                                        'Could not save. Check your values and financial limits.';
                                  });
                                }
                              },
                        child: Text(saving ? 'Saving…' : 'Confirm')),
                  ],
                )));
    for (final c in fields.values) {
      c.dispose();
    }
  }

  Future<void> stressTest() => inputDialog(
      'Compare a stress scenario',
      'These are your hypothetical assumptions, not forecasts. The saved goals and budgets will not change.',
      {
        'currency': 'Currency',
        'monthly_capacity_reduction': 'Monthly savings capacity reduction',
        'unexpected_expense': 'One-off unexpected expense',
        'annual_cost_growth_percent': 'Hypothetical annual goal cost growth (%)'
      },
      {
        'currency': 'EUR',
        'monthly_capacity_reduction': '0',
        'unexpected_expense': '0',
        'annual_cost_growth_percent': '0'
      },
      (values) => widget.mutate('plans/scenario', values));

  Future<void> editCoverage([int? index]) {
    final policies = (prefs['insurance_policies'] as List? ?? [])
        .map((p) => Map<String, dynamic>.from(p))
        .toList();
    final old = index == null ? <String, dynamic>{} : policies[index];
    return inputDialog(
        'Declared insurance coverage',
        'Use a short label, not policy numbers or personal identifiers. Only the insurer can confirm actual coverage and exclusions.',
        {
          'label': 'Policy label',
          'scope': 'Covered interest (e.g. family travel, main home)',
          'risks': 'Covered risks (comma separated)',
          'start_date': 'Start date (YYYY-MM-DD)',
          'end_date': 'End date (YYYY-MM-DD)'
        },
        {
          for (final k in ['label', 'scope', 'start_date', 'end_date'])
            k: old[k] ?? '',
          'risks': (old['risks'] as List? ?? []).join(', ')
        }, (values) async {
      final policy = <String, dynamic>{
        ...values,
        'risks': values['risks']!
            .split(',')
            .map((s) => s.trim())
            .where((s) => s.isNotEmpty)
            .toList()
      };
      if (index == null) {
        policies.add(policy);
      } else {
        policies[index] = policy;
      }
      return widget
          .mutate('preferences', {...prefs, 'insurance_policies': policies});
    });
  }

  Future<void> confirmDelete(String title, String path) async {
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text(title),
              content: const Text(
                  'This removes the selected information from LifeFlow.'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Keep')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Delete')),
              ],
            ));
    if (confirmed == true) await widget.mutate(path, {});
  }

  Future<void> editGoal([Map<String, dynamic>? goal]) async {
    final fields = <String, TextEditingController>{
      for (final key in [
        'title',
        'category',
        'description',
        'motivation',
        'currency',
        'target_amount',
        'target_date',
        'allocated_savings',
        'monthly_contribution'
      ])
        key: TextEditingController(
            text: (goal?[key] ??
                    (key == 'currency'
                        ? 'EUR'
                        : key == 'category'
                            ? 'personal'
                            : ['allocated_savings', 'monthly_contribution']
                                    .contains(key)
                                ? '0'
                                : ''))
                .toString()),
    };
    var priority = (goal?['priority'] ?? 3) as int;
    var primary = goal?['primary'] == true;
    var basis = (goal?['amount_basis'] ?? 'goal_budget') as String;
    var status = (goal?['status'] ?? 'active') as String;
    var flexibility = (goal?['flexibility'] ?? 'flexible') as String;
    var saving = false;
    String? error;
    await showEditor(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title:
                      Text(goal == null ? 'Create a goal' : 'Edit your goal'),
                  content: SizedBox(
                      width: 440,
                      child: SingleChildScrollView(
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                        const Text(
                            'Start with a title. Other details can be added later. No money is moved.'),
                        for (final key in fields.keys)
                          Padding(
                              padding: const EdgeInsets.only(top: 12),
                              child: TextField(
                                controller: fields[key],
                                enabled: !saving,
                                decoration: InputDecoration(
                                    labelText: {
                                      'title': 'Goal title',
                                      'category':
                                          'Category (housing, retirement, hobby, or your own)',
                                      'description':
                                          'What would you like to achieve?',
                                      'motivation': 'Why does it matter?',
                                      'currency': 'Currency',
                                      'target_amount':
                                          'Target amount (optional)',
                                      'target_date':
                                          'Target date (YYYY-MM-DD, optional)',
                                      'allocated_savings':
                                          'Savings allocated to this goal',
                                      'monthly_contribution':
                                          'Planned monthly contribution',
                                    }[key],
                                    border: const OutlineInputBorder()),
                              )),
                        DropdownButtonFormField<String>(
                            isExpanded: true,
                            initialValue: basis,
                            decoration: const InputDecoration(
                                labelText: 'What does the target represent?'),
                            items: const [
                              DropdownMenuItem(
                                  value: 'goal_budget',
                                  child: Text('Goal budget')),
                              DropdownMenuItem(
                                  value: 'property_deposit',
                                  child: Text('Own funding / deposit')),
                              DropdownMenuItem(
                                  value: 'property_price',
                                  child:
                                      Text('Property price — clarify deposit')),
                              DropdownMenuItem(
                                  value: 'non_financial',
                                  child: Text('Non-financial goal')),
                            ],
                            onChanged: saving
                                ? null
                                : (v) => update(() => basis = v!)),
                        DropdownButtonFormField<int>(
                            isExpanded: true,
                            initialValue: priority,
                            decoration: const InputDecoration(
                                labelText: 'Priority (1 is highest)'),
                            items: [
                              for (var i = 1; i <= 5; i++)
                                DropdownMenuItem(value: i, child: Text('$i'))
                            ],
                            onChanged: saving
                                ? null
                                : (v) => update(() => priority = v!)),
                        SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            title: const Text('My primary goal'),
                            value: primary,
                            onChanged: saving
                                ? null
                                : (v) => update(() => primary = v)),
                        DropdownButtonFormField<String>(
                            isExpanded: true,
                            initialValue: status,
                            decoration:
                                const InputDecoration(labelText: 'Status'),
                            items: ['active', 'paused', 'completed']
                                .map((v) =>
                                    DropdownMenuItem(value: v, child: Text(v)))
                                .toList(),
                            onChanged: saving
                                ? null
                                : (v) => update(() => status = v!)),
                        DropdownButtonFormField<String>(
                            isExpanded: true,
                            initialValue: flexibility,
                            decoration:
                                const InputDecoration(labelText: 'Flexibility'),
                            items: ['flexible', 'fixed']
                                .map((v) =>
                                    DropdownMenuItem(value: v, child: Text(v)))
                                .toList(),
                            onChanged: saving
                                ? null
                                : (v) => update(() => flexibility = v!)),
                        if (error != null)
                          Text(error!,
                              style: const TextStyle(color: Colors.red)),
                      ]))),
                  actions: [
                    TextButton(
                        onPressed: saving ? null : () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: saving
                            ? null
                            : () async {
                                if (fields['title']!.text.trim().isEmpty) {
                                  update(() =>
                                      error = 'Please give your goal a title.');
                                  return;
                                }
                                update(() => saving = true);
                                final body = <String, dynamic>{
                                  for (final key in fields.keys)
                                    key: fields[key]!.text.trim(),
                                  'priority': priority,
                                  'primary': primary,
                                  'status': status,
                                  'flexibility': flexibility,
                                  'amount_basis': basis,
                                  'target_amount': fields['target_amount']!
                                          .text
                                          .trim()
                                          .isEmpty
                                      ? null
                                      : fields['target_amount']!.text.trim(),
                                  'target_date':
                                      fields['target_date']!.text.trim().isEmpty
                                          ? null
                                          : fields['target_date']!.text.trim(),
                                  if (goal != null)
                                    'expected_revision': goal['revision'],
                                };
                                final ok = await widget.mutate(
                                    goal == null
                                        ? 'goals'
                                        : 'goals/${goal['id']}',
                                    body);
                                if (!context.mounted) return;
                                if (ok) {
                                  Navigator.pop(context);
                                } else {
                                  update(() {
                                    saving = false;
                                    error =
                                        'Could not save. Check the date, amounts and shared savings allocation.';
                                  });
                                }
                              },
                        child: Text(saving ? 'Saving…' : 'Save goal')),
                  ],
                )));
    for (final c in fields.values) {
      c.dispose();
    }
  }

  Future<void> editPreferences() async {
    final values = prefs;
    final budgets = (values['budgets'] as List)
        .map((b) => Map<String, dynamic>.from(b))
        .toList();
    final controllers = {
      for (final key in [
        'allowed_categories',
        'protected_categories',
        'declined_questions'
      ])
        key: TextEditingController(text: (values[key] as List).join(', ')),
      'interval': TextEditingController(
          text: '${values['recommendation_interval_hours']}'),
      'increase': TextEditingController(
          text: '${values['subscription_increase_percent']}'),
      'income': TextEditingController(
          text: '${values['income_change_percent'] ?? 10}'),
      'renewal':
          TextEditingController(text: '${values['renewal_notice_days']}'),
    };
    final budgetText = TextEditingController(
        text: const JsonEncoder.withIndent('  ').convert(budgets));
    final currency = TextEditingController(
        text: budgets.isEmpty ? 'EUR' : '${budgets[0]['currency']}');
    final pool = TextEditingController(
        text: budgets.isEmpty ? '' : '${budgets[0]['savings_pool'] ?? ''}');
    final reserve = TextEditingController(
        text: budgets.isEmpty ? '0' : '${budgets[0]['emergency_reserve']}');
    final capacity = TextEditingController(
        text: budgets.isEmpty ? '' : '${budgets[0]['monthly_capacity'] ?? ''}');
    var advanced = false;
    var saving = false;
    String? error;
    await showEditor(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: const Text('Preferences and financial limits'),
                  content: SizedBox(
                      width: 460,
                      child: SingleChildScrollView(
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                        const Text(
                            'Only declare money you consider available. Keep your emergency reserve separate.'),
                        for (final entry in {
                          'memory_enabled': 'Remember confirmed information',
                          'notifications': 'Show in-app suggestions',
                          'reminders': 'Renewal reminders',
                          'budget_optimization':
                              'I want to review optional expenses'
                        }.entries)
                          SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text(entry.value),
                              value: values[entry.key] == true,
                              onChanged: saving
                                  ? null
                                  : (v) => update(() => values[entry.key] = v)),
                        const Text(
                            'Turning memory off deletes conversation history and pending proposals. Existing goals and confirmed information remain available for review and deletion.'),
                        for (final entry in {
                          'proactivity': ['off', 'low', 'normal'],
                          'financing_preference': [
                            'unspecified',
                            'saving',
                            'compare'
                          ],
                          'communication': ['in_app', 'branch', 'phone'],
                        }.entries)
                          DropdownButtonFormField<String>(
                              isExpanded: true,
                              initialValue: values[entry.key],
                              decoration: InputDecoration(
                                  labelText: entry.key.replaceAll('_', ' ')),
                              items: entry.value
                                  .map((v) => DropdownMenuItem(
                                      value: v, child: Text(v)))
                                  .toList(),
                              onChanged: saving
                                  ? null
                                  : (v) => update(() => values[entry.key] = v)),
                        for (final entry in controllers.entries)
                          TextField(
                              controller: entry.value,
                              decoration: InputDecoration(
                                  labelText: {
                                'allowed_categories':
                                    'Allowed categories (blank = all; comma separated)',
                                'protected_categories':
                                    'Categories not to optimise (comma separated)',
                                'declined_questions':
                                    'Topics you do not want questions about',
                                'interval':
                                    'Minimum hours between new suggestions',
                                'income': 'Income change alert threshold (%)',
                                'increase':
                                    'Subscription increase alert threshold (%)',
                                'renewal': 'Days before a declared renewal',
                              }[entry.key])),
                        for (final entry in {
                          currency: 'Currency',
                          pool: 'Total available savings pool',
                          reserve: 'Protected emergency reserve',
                          capacity: 'Monthly savings capacity'
                        }.entries)
                          TextField(
                              controller: entry.key,
                              decoration:
                                  InputDecoration(labelText: entry.value)),
                        SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            title: const Text('Edit multiple currency budgets'),
                            value: advanced,
                            onChanged: (v) => update(() => advanced = v)),
                        if (advanced)
                          TextField(
                              controller: budgetText,
                              maxLines: 8,
                              decoration: const InputDecoration(
                                  labelText:
                                      'Currency budgets (JSON; no exchange rates assumed)')),
                        if (error != null)
                          Text(error!,
                              style: const TextStyle(color: Colors.red)),
                      ]))),
                  actions: [
                    TextButton(
                        onPressed: saving ? null : () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: saving
                            ? null
                            : () async {
                                try {
                                  for (final key in [
                                    'allowed_categories',
                                    'protected_categories',
                                    'declined_questions'
                                  ]) {
                                    values[key] = controllers[key]!
                                        .text
                                        .split(',')
                                        .map((s) => s.trim())
                                        .where((s) => s.isNotEmpty)
                                        .toList();
                                  }
                                  values['recommendation_interval_hours'] =
                                      int.parse(controllers['interval']!.text);
                                  values['subscription_increase_percent'] =
                                      int.parse(controllers['increase']!.text);
                                  values['income_change_percent'] =
                                      int.parse(controllers['income']!.text);
                                  values['renewal_notice_days'] =
                                      int.parse(controllers['renewal']!.text);
                                  values['budgets'] = advanced
                                      ? jsonDecode(budgetText.text)
                                      : [
                                          {
                                            'currency': currency.text.trim(),
                                            'savings_pool':
                                                pool.text.trim().isEmpty
                                                    ? null
                                                    : pool.text.trim(),
                                            'emergency_reserve':
                                                reserve.text.trim(),
                                            'monthly_capacity':
                                                capacity.text.trim().isEmpty
                                                    ? null
                                                    : capacity.text.trim()
                                          },
                                          ...budgets.skip(1),
                                        ];
                                  update(() => saving = true);
                                  final ok = await widget.mutate(
                                      'preferences', values);
                                  if (!context.mounted) return;
                                  if (ok) {
                                    Navigator.pop(context);
                                  } else {
                                    update(() {
                                      saving = false;
                                      error =
                                          'Could not save. Check budgets and existing allocations.';
                                    });
                                  }
                                } catch (_) {
                                  update(() => error =
                                      'Please check numeric fields and currency budgets.');
                                }
                              },
                        child: Text(saving ? 'Saving…' : 'Save preferences')),
                  ],
                )));
    for (final c in [
      ...controllers.values,
      budgetText,
      currency,
      pool,
      reserve,
      capacity
    ]) {
      c.dispose();
    }
  }

  Future<void> editInformation(String key, dynamic current) async {
    final controller = TextEditingController(
        text: current == null
            ? ''
            : current is String
                ? current
                : jsonEncode(current));
    var saving = false;
    String? error;
    await showEditor(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: Text(key.replaceAll('_', ' ')),
                  content: Column(mainAxisSize: MainAxisSize.min, children: [
                    TextField(
                        controller: controller,
                        maxLines: 3,
                        maxLength: 500,
                        decoration: const InputDecoration(
                            labelText:
                                'Your confirmed information (blank to delete)')),
                    if (error != null)
                      Text(error!, style: const TextStyle(color: Colors.red)),
                  ]),
                  actions: [
                    TextButton(
                        onPressed: saving ? null : () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: saving
                            ? null
                            : () async {
                                dynamic value = controller.text.trim();
                                if (key == 'dependents_count') {
                                  value = int.tryParse(value);
                                }
                                if (key == 'monthly_income') {
                                  try {
                                    value = jsonDecode(value);
                                  } catch (_) {
                                    update(() => error =
                                        'Use a money object, e.g. {"amount":"2500","currency":"EUR"}.');
                                    return;
                                  }
                                }
                                update(() => saving = true);
                                final ok = await widget.mutate('context', {
                                  'attributes': {
                                    key: controller.text.trim().isEmpty
                                        ? null
                                        : {
                                            'value': value,
                                            'source': 'declared',
                                            'observed_at': DateTime.now()
                                                .toUtc()
                                                .toIso8601String()
                                                .substring(0, 10),
                                            if (key ==
                                                'investment_risk_profile')
                                              'valid_until': DateTime.now()
                                                  .toUtc()
                                                  .add(const Duration(days: 90))
                                                  .toIso8601String()
                                                  .substring(0, 10),
                                          }
                                  }
                                });
                                if (!context.mounted) return;
                                if (ok) {
                                  Navigator.pop(context);
                                } else {
                                  update(() {
                                    saving = false;
                                    error =
                                        'Please check the value for this field.';
                                  });
                                }
                              },
                        child:
                            Text(saving ? 'Saving…' : 'Confirm information')),
                  ],
                )));
    controller.dispose();
  }

  Future<void> reviewSubscription(Map<String, dynamic> sub) async {
    final values = <String, dynamic>{
      for (final k in [
        'confirmed',
        'frequency',
        'usage_status',
        'importance',
        'customer_decision',
        'renewal_date',
        'goal_id'
      ])
        k: sub[k],
    };
    final renewal = TextEditingController(text: sub['renewal_date'] ?? '');
    var saving = false;
    String? error;
    await showEditor(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                  title: Text(sub['merchant']),
                  content: SizedBox(
                      width: 420,
                      child: SingleChildScrollView(
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                        Text(
                            '${amount(sub['amount'], sub['currency'])} per observed payment. A payment does not tell us whether you use the service.'),
                        SwitchListTile(
                            title:
                                const Text('I confirm this is a subscription'),
                            value: values['confirmed'] == true,
                            onChanged: (v) =>
                                update(() => values['confirmed'] = v)),
                        for (final entry in {
                          'frequency': ['unknown', 'monthly', 'annual'],
                          'usage_status': [
                            'unknown',
                            'regularly',
                            'occasionally',
                            'rarely',
                            'unused',
                            'do_not_ask'
                          ],
                          'importance': [
                            'unknown',
                            'essential',
                            'valued',
                            'optional'
                          ],
                          'customer_decision': [
                            'undecided',
                            'keep',
                            'review',
                            'consider_cancelling'
                          ],
                        }.entries)
                          DropdownButtonFormField<String>(
                              isExpanded: true,
                              initialValue: values[entry.key],
                              decoration: InputDecoration(
                                  labelText: entry.key.replaceAll('_', ' ')),
                              items: entry.value
                                  .map((v) => DropdownMenuItem(
                                      value: v,
                                      child: Text(v.replaceAll('_', ' '))))
                                  .toList(),
                              onChanged: (v) =>
                                  update(() => values[entry.key] = v)),
                        TextField(
                            controller: renewal,
                            decoration: const InputDecoration(
                                labelText:
                                    'Known renewal date (YYYY-MM-DD, optional)')),
                        DropdownButtonFormField<int>(
                            isExpanded: true,
                            initialValue: values['goal_id'],
                            decoration: const InputDecoration(
                                labelText:
                                    'Consider potential savings for a goal'),
                            items: [
                              const DropdownMenuItem<int>(
                                  value: null, child: Text('No goal')),
                              for (final g in widget.data['goals'] as List)
                                if (g['currency'] == sub['currency'])
                                  DropdownMenuItem<int>(
                                      value: g['id'], child: Text(g['title']))
                            ],
                            onChanged: (v) =>
                                update(() => values['goal_id'] = v)),
                        const Text(
                            'This is a recorded preference only. No subscription is cancelled and no savings are allocated automatically.'),
                        if (error != null)
                          Text(error!,
                              style: const TextStyle(color: Colors.red)),
                      ]))),
                  actions: [
                    TextButton(
                        onPressed: saving ? null : () => Navigator.pop(context),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: saving
                            ? null
                            : () async {
                                values['renewal_date'] =
                                    renewal.text.trim().isEmpty
                                        ? null
                                        : renewal.text.trim();
                                update(() => saving = true);
                                final ok = await widget.mutate(
                                    'subscriptions/${sub['id']}', values);
                                if (!context.mounted) return;
                                if (ok) {
                                  Navigator.pop(context);
                                } else {
                                  update(() {
                                    saving = false;
                                    error =
                                        'Could not save this review. Check the renewal date.';
                                  });
                                }
                              },
                        child: Text(saving ? 'Saving…' : 'Save review')),
                  ],
                )));
    renewal.dispose();
  }

  Widget goalCard(Map<String, dynamic> g) {
    final plans = widget.data['plan']['goals'] as List;
    final matches = plans.where((p) => p['goal_id'] == g['id']);
    final p = matches.isEmpty ? null : matches.first;
    return Card(
        child: Padding(
            padding: const EdgeInsets.all(14),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(g['title'], style: Theme.of(context).textTheme.titleMedium),
              Text(
                  '${g['category']} · Priority ${g['priority']} · ${g['status']}${g['primary'] == true ? ' · Primary' : ''}'),
              if ((g['motivation'] as String).isNotEmpty) Text(g['motivation']),
              Text(
                  'Target: ${amount(g['target_amount'], g['currency'])} · ${g['target_date'] ?? 'No deadline yet'}'),
              Text(
                  'Allocated: ${amount(g['allocated_savings'], g['currency'])}'),
              if (p?['progress_percent'] != null) ...[
                const SizedBox(height: 8),
                LinearProgressIndicator(
                    value: (p['progress_percent'] as num).toDouble() / 100),
                Text(
                    '${(p['progress_percent'] as num).toStringAsFixed(0)}% funded'),
              ],
              if (p != null) ...[
                Text('Plan: ${(p['status'] as String).replaceAll('_', ' ')}'),
                Text(
                    'Monthly amount needed: ${amount(p['monthly_required'], g['currency'])}'),
                Text(
                    'Gap versus your planned contribution: ${amount(p['monthly_gap'], g['currency'])}'),
                ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    title: const Text('Financial scenarios'),
                    children: [
                      for (final s in p['scenarios'] as List)
                        ListTile(
                            title: Text(
                                (s['type'] as String).replaceAll('_', ' ')),
                            subtitle: Text(s['type'] == 'extend_deadline'
                                ? '${s['months']} months at ${amount(s['monthly'], g['currency'])}/month'
                                : s['type'] == 'adjust_target'
                                    ? 'Reach ${amount(s['target'], g['currency'])} by the chosen date'
                                    : s['type'] == 'priority_allocation'
                                        ? '${amount(s['monthly'], g['currency'])}/month from shared capacity. Proposal only.'
                                        : s['note'] ?? '')),
                      const Text(
                          'Zero-return simulations. No bank offer, transfer or guaranteed investment return.'),
                    ]),
              ],
              Wrap(spacing: 8, children: [
                TextButton(
                    onPressed: blocked ? null : () => editGoal(g),
                    child: const Text('Edit goal')),
                TextButton(
                    onPressed: () => confirmDelete(
                        'Delete this goal?', 'goals/${g['id']}/delete'),
                    child: const Text('Delete')),
              ]),
            ])));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final goals = widget.data['goals'] as List;
    final information = widget.data['information'] as Map;
    final subs = widget.data['subscriptions'] as List;
    final services = widget.data['services'] as List;
    final categories = goals
        .where((g) => g['status'] == 'active')
        .map((g) => g['category'])
        .toSet();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      for (final n in widget.data['notifications'] as List? ?? [])
        Card(
            child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('LifeFlow reminder · based on your settings',
                          style:
                              TextStyle(fontSize: 12, color: Colors.blueGrey)),
                      Text(n['message']),
                      Wrap(children: [
                        TextButton(
                            onPressed: () => widget.mutate(
                                'recommendations/${n['id']}/feedback',
                                {'status': 'postponed'}),
                            child: const Text('Remind me in 7 days')),
                        TextButton(
                            onPressed: () => widget.mutate(
                                'recommendations/${n['id']}/feedback',
                                {'status': 'dismissed'}),
                            child: const Text('Dismiss')),
                      ]),
                    ]))),
      if (blocked)
        const Padding(
            padding: EdgeInsets.all(12),
            child: Text(
                'Personalization is paused. You can still review and delete your saved information.')),
      Card(
          child: ExpansionTile(
              title: const Text('My Goals'),
              initiallyExpanded: true,
              childrenPadding: const EdgeInsets.all(12),
              children: [
            if (goals.isEmpty)
              const Text(
                  'What would you like to achieve? Start a goal here or discuss it in the chat.'),
            ...goals.map((g) => goalCard(Map<String, dynamic>.from(g))),
            FilledButton.icon(
                onPressed: blocked ? null : () => editGoal(),
                icon: const Icon(Icons.add),
                label: const Text('Create a goal')),
            for (final estimate
                in widget.data['estimated_monthly_surplus'] as List? ?? [])
              Text(
                  'Observed monthly surplus: ${amount(estimate['amount'], estimate['currency'])}. Indicative only; confirm your own savings capacity in preferences.'),
            TextButton(
                onPressed: blocked ? null : stressTest,
                child: const Text('Compare a stress scenario')),
            if (widget.data['plan']['stress_scenario'] != null)
              ExpansionTile(
                  title: const Text('Your saved stress scenario'),
                  children: [
                    const Text(
                        'Customer-entered assumptions. No changes to your goals or budget.'),
                    for (final p in widget.data['plan']['stress_scenario']
                        ['goals'] as List)
                      ListTile(
                          title: Text(p['title']),
                          subtitle: Text(
                              'Monthly amount needed: ${amount(p['monthly_required'], p['currency'])}')),
                    for (final warning in widget.data['plan']['stress_scenario']
                        ['warnings'] as List)
                      Text(warning),
                  ]),
            for (final warning in widget.data['plan']['warnings'] as List)
              Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(warning,
                      style: const TextStyle(color: Colors.deepOrange))),
          ])),
      Card(
          child: ExpansionTile(
              title: const Text('My Information and Preferences'),
              childrenPadding: const EdgeInsets.all(12),
              children: [
            const Text(
                'You choose what to share. Edit a field, clear it to delete it, or leave it unanswered.'),
            for (final entry in information.entries)
              ListTile(
                  title: Text((entry.key as String).replaceAll('_', ' ')),
                  subtitle: Text(
                      '${entry.value['value']} · ${entry.value['source']} · ${entry.value['observed_at']}'),
                  trailing: IconButton(
                      tooltip: 'Edit information',
                      icon: const Icon(Icons.edit),
                      onPressed: () =>
                          editInformation(entry.key, entry.value['value']))),
            DropdownButtonFormField<String>(
                isExpanded: true,
                decoration: const InputDecoration(
                    labelText: 'Add information when useful'),
                items: [
                  for (final key in [
                    'housing_status',
                    'region',
                    'personal_constraints',
                    'hobbies',
                    'passions',
                    'interests',
                    'ambitions',
                    'life_priorities',
                    'financial_constraints',
                    'insurance_coverage',
                    'retirement_preferences',
                    'employment_status',
                    'household_status',
                    'dependents_count',
                    'monthly_income'
                  ])
                    DropdownMenuItem(
                        value: key, child: Text(key.replaceAll('_', ' ')))
                ],
                onChanged: blocked
                    ? null
                    : (v) {
                        if (v != null) {
                          editInformation(v, information[v]?['value']);
                        }
                      }),
            TextButton(
                onPressed: blocked ? null : editPreferences,
                child: const Text('Preferences and financial limits')),
            TextButton(
                onPressed: blocked ? null : () => editCoverage(),
                child: const Text('Add declared insurance coverage')),
            for (final entry
                in (prefs['insurance_policies'] as List? ?? []).asMap().entries)
              ListTile(
                  title: Text(entry.value['label']),
                  subtitle: Text(
                      '${entry.value['scope']} · ${(entry.value['risks'] as List).join(', ')}'),
                  onTap: blocked ? null : () => editCoverage(entry.key),
                  trailing: IconButton(
                      tooltip: 'Remove declared coverage',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: blocked
                          ? null
                          : () {
                              final updated = List<dynamic>.from(
                                  prefs['insurance_policies'])
                                ..removeAt(entry.key);
                              widget.mutate('preferences',
                                  {...prefs, 'insurance_policies': updated});
                            })),
            for (final overlap in widget.data['coverage_checks'] as List? ?? [])
              Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(
                      '${(overlap['policies'] as List).join(' / ')}: ${overlap['message']}')),
            TextButton(
                onPressed: () => confirmDelete(
                    'Delete remembered information and chat?', 'memory/delete'),
                child: const Text('Delete remembered information')),
            TextButton(
                onPressed: () => confirmDelete(
                    'Delete all goals, preferences and LifeFlow memory, and pause personalization?',
                    'life/delete'),
                child: const Text('Delete all LifeFlow personal data')),
          ])),
      Card(
          child: ExpansionTile(
              title: const Text('My Subscriptions'),
              childrenPadding: const EdgeInsets.all(12),
              children: [
            const Text(
                'Possible recurring expenses. We do not know whether a service is useful to you until you tell us.'),
            if (subs.isEmpty)
              const Text(
                  'No recurring payments detected in the authorised history yet.'),
            for (final s in subs)
              ListTile(
                  isThreeLine: true,
                  title: Text(s['merchant']),
                  subtitle: Text(
                      '${amount(s['amount'], s['currency'])} per payment\n${s['confirmed'] == true ? 'Annual cost: ${amount(s['estimated_annual_cost'], s['currency'])}' : '${s['detected_frequency']} pattern inferred; annual projection: ${amount(s['projected_annual_cost'], s['currency'])}'}\nUse: ${s['usage_status']}${s['potential_annual_saving'] != null ? '\nPotential saving only: ${amount(s['potential_annual_saving'], s['currency'])}/year' : ''}'),
                  trailing: IconButton(
                      tooltip: 'Review subscription',
                      icon: const Icon(Icons.edit_note),
                      onPressed: () =>
                          reviewSubscription(Map<String, dynamic>.from(s)))),
          ])),
      Card(
          child: ExpansionTile(
              title: const Text('Relevant ecosystem services'),
              childrenPadding: const EdgeInsets.all(12),
              children: [
            const Text(
                'Official information, not a quote or connected partner integration. You decide whether to explore further.'),
            if (categories.isEmpty)
              const Text('Add a goal to see relevant services.'),
            for (final s in services)
              if ((s['supported_goal_categories'] as List)
                  .any(categories.contains))
                ListTile(
                    title: Text(s['name']),
                    subtitle: Text(
                        '${s['description']}\nVerified: ${s['last_verified_at'] ?? 'Not verified'} · ${s['integration_status']}'),
                    onTap: () => showEditor(
                        context: context,
                        builder: (context) => AlertDialog(
                                title: Text(s['name']),
                                content: SingleChildScrollView(
                                    child: Column(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                      Text(s['description']),
                                      Text(
                                          'Information needed: ${(s['required_information'] as List).join(', ')}'),
                                      if (s['verified_url'] != null)
                                        SelectableText(s['verified_url']),
                                      const Text(
                                          'Source link supplied by the curated catalogue, not generated by the AI. No application is submitted here.'),
                                    ])),
                                actions: [
                                  TextButton(
                                      onPressed: () => Navigator.pop(context),
                                      child: const Text('Close'))
                                ]))),
          ])),
    ]);
  }
}
