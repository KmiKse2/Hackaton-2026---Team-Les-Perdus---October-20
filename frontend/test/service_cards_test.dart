import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lifeflow/service_cards.dart';
import 'package:lifeflow/recommendations.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'dart:convert';
import 'recommendations_test.dart' as fixtures;

Map<String, dynamic> service() => {
      'id': 'kbc_myhome',
      'name': 'MyHome',
      'provider': 'KBC',
      'ecosystem': 'Housing',
      'why_recommended': 'Explore information for your housing project.',
      'requires_confirmation': true,
      'destination_available': true,
      'destination_status': 'verified',
      'verification_status': 'official_source',
      'destination_url': 'https://www.kbc.be/particuliers/fr/thema/myhome.html',
    };

Widget screen(Widget child) =>
    MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  testWidgets('responsive service card navigates only after a click',
      (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 800));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final opened = <Uri>[];
    await tester.pumpWidget(screen(ServiceCards(
        services: [service()],
        launch: (uri) async {
          opened.add(uri);
          return true;
        })));
    expect(find.text('MyHome'), findsOneWidget);
    expect(find.text('KBC · Housing'), findsOneWidget);
    expect(find.textContaining('still needs confirmation'), findsOneWidget);
    expect(opened, isEmpty);
    await tester.pumpWidget(screen(ServiceCards(
        services: [service()],
        launch: (uri) async {
          opened.add(uri);
          return true;
        })));
    expect(opened, isEmpty);
    await tester.tap(find.text('Learn more'));
    await tester.pumpAndSettle();
    expect(opened.single.toString(), service()['destination_url']);
    expect(opened.single.hasQuery, isFalse);
    expect(tester.takeException(), isNull);
  });

  testWidgets('missing destination has an informational state', (tester) async {
    await tester.pumpWidget(screen(ServiceCards(services: [
      {...service(), 'destination_available': false, 'destination_url': null}
    ])));
    expect(find.text('Learn more'), findsNothing);
    expect(find.textContaining('not yet verified'), findsOneWidget);
  });

  test('unsafe and unverified destinations never navigate', () {
    for (final url in [
      'javascript:alert(1)',
      'http://www.kbc.be/',
      'https://www.kbc.be.evil.test/',
      'https://www.kbc.be@evil.test/',
      'https://user@www.kbc.be/',
      'https://www.kbc.be:8443/',
      'https://www.kbc.be/?customer=123',
      'https://www.kbc.be/#private',
      'https://www.kbc.be\\@evil.test/'
    ]) {
      expect(
          officialDestination({...service(), 'destination_url': url}), isNull);
    }
    expect(
        officialDestination({...service(), 'destination_status': 'unverified'}),
        isNull);
  });

  testWidgets('failed opening reports an error and dismissal waits for success',
      (tester) async {
    var success = false;
    await tester.pumpWidget(screen(ServiceCards(
        services: [service()],
        launch: (_) async => false,
        dismiss: (_) async => success)));
    await tester.tap(find.text('Learn more'));
    await tester.pump();
    expect(find.textContaining('Unable to open'), findsOneWidget);
    await tester.tap(find.text('Hide this service'));
    await tester.pump();
    expect(find.text('MyHome'), findsOneWidget);
    success = true;
    await tester.tap(find.text('Hide this service'));
    await tester.pump();
    expect(find.text('MyHome'), findsNothing);
  });

  testWidgets('API enrichment renders and survives identical snapshot rebuilds',
      (tester) async {
    var requests = 0;
    final data = fixtures.response();
    data['recommendations'][0]['matched_services'] = [service()];
    final client = MockClient((request) async {
      requests++;
      expect(request.url.path, '/api/customers/1/ai-analysis');
      return http.Response(jsonEncode(data), 200);
    });
    addTearDown(client.close);
    final api = RecommendationApi(client, 'http://localhost', '');
    Widget render() => screen(RecommendationSection(
        snapshot: fixtures.snapshot(), load: api.analyze));
    await tester.pumpWidget(render());
    await tester.pumpAndSettle();
    expect(find.text('MyHome'), findsOneWidget);
    await tester.pumpWidget(render());
    await tester.pumpAndSettle();
    expect(find.text('MyHome'), findsOneWidget);
    expect(requests, 1);
  });

  testWidgets('server exclusions suppress services across surfaces',
      (tester) async {
    await tester.pumpWidget(screen(
        ServiceCards(services: [service()], excluded: const ['kbc_myhome'])));
    expect(find.text('MyHome'), findsNothing);
    expect(find.text('Learn more'), findsNothing);
  });
}
