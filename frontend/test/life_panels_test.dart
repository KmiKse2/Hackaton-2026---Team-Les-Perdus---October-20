import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lifeflow/life_panels.dart';

Map<String, dynamic> lifeData({bool goal = false}) => {
      'blocked': false,
      'goals': goal
          ? [
              {
                'id': 7,
                'revision': 1,
                'title': 'Buy a home',
                'category': 'housing',
                'description': '',
                'motivation': 'More space',
                'currency': 'EUR',
                'target_amount': '30000',
                'target_date': '2031-10-20',
                'allocated_savings': '6000',
                'monthly_contribution': '300',
                'priority': 1,
                'primary': true,
                'status': 'active',
                'flexibility': 'flexible',
                'amount_basis': 'property_deposit',
              }
            ]
          : [],
      'plan': {
        'goals': goal
            ? [
                {
                  'goal_id': 7,
                  'progress_percent': 20,
                  'status': 'gap',
                  'monthly_required': '400',
                  'monthly_gap': '100',
                  'scenarios': [
                    {'type': 'extend_deadline', 'months': 80, 'monthly': '300'}
                  ],
                }
              ]
            : [],
        'warnings': []
      },
      'preferences': {
        'budgets': [],
        'insurance_policies': [],
        'memory_enabled': true,
        'notifications': true,
        'reminders': true,
        'budget_optimization': false,
        'proactivity': 'normal',
        'financing_preference': 'saving',
        'communication': 'in_app',
        'allowed_categories': [],
        'protected_categories': [],
        'declined_questions': [],
        'recommendation_interval_hours': 24,
        'subscription_increase_percent': 10,
        'renewal_notice_days': 30
      },
      'information': {},
      'subscriptions': [],
      'services': [],
      'notifications': [],
    };

void main() {
  testWidgets('goals and backend scenarios render on a narrow screen',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(390, 850));
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: LifePanels(
                    data: lifeData(goal: true),
                    mutate: (path, body) async => true)))));
    expect(find.text('Buy a home'), findsOneWidget);
    expect(find.text('20% funded'), findsOneWidget);
    expect(find.text('Monthly amount needed: 400 EUR'), findsOneWidget);
    await tester.tap(find.text('Financial scenarios'));
    await tester.pumpAndSettle();
    expect(find.text('80 months at 300 EUR/month'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.binding.setSurfaceSize(null);
  });

  testWidgets('create goal sends a confirmed customer command, no Gemini call',
      (tester) async {
    String? path;
    Map<String, dynamic>? sent;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: LifePanels(
                    data: lifeData(),
                    mutate: (p, b) async {
                      path = p;
                      sent = b;
                      return true;
                    })))));
    await tester.tap(find.text('Create a goal'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Goal title'), 'Cycle across Belgium');
    await tester.tap(find.text('Save goal'));
    await tester.pumpAndSettle();
    expect(path, 'goals');
    expect(sent?['title'], 'Cycle across Belgium');
    expect(sent?['target_amount'], isNull);
    expect(sent?['allocated_savings'], '0');
    expect(tester.takeException(), isNull);
  });

  testWidgets('failed mutations keep the goal form and error visible',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: LifePanels(
                    data: lifeData(), mutate: (p, b) async => false)))));
    await tester.tap(find.text('Create a goal'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Goal title'), 'Travel');
    await tester.tap(find.text('Save goal'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Could not save.'), findsOneWidget);
    expect(find.text('Travel'), findsOneWidget);
  });
}
