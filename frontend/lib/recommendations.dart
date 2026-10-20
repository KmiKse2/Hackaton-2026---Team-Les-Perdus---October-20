import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

/// One stateless analysis request, with no chat message or client-side credentials.
class RecommendationApi {
  RecommendationApi(this.client, this.baseUrl, this.demoToken);
  final http.Client client;
  final String baseUrl;
  final String demoToken;

  Future<Map<String, dynamic>> analyze(int customerId) async {
    final response = await client.post(
      Uri.parse('$baseUrl/api/customers/$customerId/ai-analysis'),
      headers: {'X-Demo-Token': demoToken, 'Content-Type': 'application/json'},
    ).timeout(const Duration(seconds: 45));
    if (response.statusCode != 200) {
      throw RecommendationFailure(switch (response.statusCode) {
        401 => 'Please check your demo access token.',
        403 =>
          'Recommendations are paused. Personalization and banking permissions are required.',
        404 => 'This customer is no longer available.',
        502 ||
        503 ||
        504 =>
          'AI recommendations are temporarily unavailable. Please try again later.',
        _ => 'Unable to load recommendations. Please try again.',
      });
    }
    final result = jsonDecode(utf8.decode(response.bodyBytes));
    if (result is! Map<String, dynamic> ||
        result['customer_id'] != customerId ||
        result['recommendations'] is! List) {
      throw const RecommendationFailure(
          'The recommendation response was invalid. Please try again.');
    }
    return result;
  }
}

class RecommendationFailure implements Exception {
  const RecommendationFailure(this.message);
  final String message;
}

class RecommendationSection extends StatefulWidget {
  const RecommendationSection({
    super.key,
    required this.snapshot,
    required this.load,
  });
  final Map<String, dynamic> snapshot;
  final Future<Map<String, dynamic>> Function(int) load;

  @override
  State<RecommendationSection> createState() => _RecommendationSectionState();
}

class _RecommendationSectionState extends State<RecommendationSection> {
  List<Map<String, dynamic>>? _cards;
  bool _loading = false;
  String? _error;
  bool _changed = false;
  int _generation = 0;

  bool get _allowed {
    final permissions =
        widget.snapshot['consent']?['effective_permissions'] as List? ?? [];
    return widget.snapshot['customer_profile']?['blocked'] == false &&
        permissions.contains('accounts') &&
        permissions.contains('transactions');
  }

  // Only context relevant to analysis; chat and poll bookkeeping do not invalidate it.
  String _contextKey(Map<String, dynamic> snapshot) => jsonEncode({
        for (final key in [
          'customer',
          'analysis_date',
          'signals',
          'events',
          'financial',
          'customer_profile',
        ])
          key: snapshot[key],
        'permissions': snapshot['consent']?['effective_permissions'],
        'accounts': snapshot['consent']?['account_ids'],
      });

  @override
  void initState() {
    super.initState();
    if (_allowed) _generate();
  }

  @override
  void didUpdateWidget(covariant RecommendationSection oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_contextKey(oldWidget.snapshot) != _contextKey(widget.snapshot)) {
      // Invalidate even in-flight results on consent, feedback or account changes.
      _generation++;
      _cards = null;
      _error = null;
      _loading = false;
      _changed = true;
      // A customer switch gets one initial request, not a poll-triggered retry.
      if (oldWidget.snapshot['customer']['id'] !=
              widget.snapshot['customer']['id'] &&
          _allowed) {
        _generate();
      }
    }
  }

  Future<void> _generate() async {
    if (_loading || !_allowed) return;
    final generation = ++_generation;
    final customerId = widget.snapshot['customer']['id'] as int;
    setState(() {
      _loading = true;
      _error = null;
      _cards = null;
      _changed = false;
    });
    try {
      final result = await widget.load(customerId);
      if (!mounted || generation != _generation || !_allowed) return;
      if (result['customer_id'] != customerId) {
        throw const RecommendationFailure(
            'The recommendation response was invalid.');
      }
      final cards = (result['recommendations'] as List).map((value) {
        final card = Map<String, dynamic>.from(value as Map);
        if (card['action_type'] != 'view_details' ||
            card['action_label'] != 'View details' ||
            card['display_priority'] is! int ||
            card['id'] is! String ||
            card['requires_confirmation'] is! bool ||
            ['title', 'message', 'description', 'justification']
                .any((key) => card[key] is! String) ||
            (card['clarification_question'] != null &&
                card['clarification_question'] is! String)) {
          throw const RecommendationFailure(
              'The recommendation response was invalid.');
        }
        return card;
      }).toList()
        ..sort((a, b) => (a['display_priority'] as int)
            .compareTo(b['display_priority'] as int));
      setState(() => _cards = cards);
    } catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() => _error = error is RecommendationFailure
          ? error.message
          : 'Unable to reach AI recommendations. Please try again.');
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('Recommendations for you',
            style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 6),
        const Text(
            'AI-generated by Gemini · Based on your authorized information',
            style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
        const SizedBox(height: 12),
        if (!_allowed)
          const Text(
              'Recommendations are paused. Personalization and banking permissions are required.')
        else if (_loading) ...[
          const LinearProgressIndicator(),
          const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Preparing your recommendations…')),
        ] else ...[
          if (_error != null)
            Text(_error!, key: const Key('recommendation-error')),
          if (_changed)
            const Text(
                'Your situation has changed. Refresh to see updated recommendations.'),
          if (_cards != null && _cards!.isEmpty)
            const Text(
                'No recommendations for now. There is not enough relevant information to suggest a next step.'),
          if (_cards != null)
            ..._cards!.map((card) =>
                RecommendationCard(key: ValueKey(card['id']), card: card)),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _generate,
              icon: const Icon(Icons.refresh),
              label: Text(_error == null
                  ? 'Refresh recommendations'
                  : 'Retry recommendations'),
            ),
          ),
        ],
      ]),
    );
  }
}

class RecommendationCard extends StatefulWidget {
  const RecommendationCard({super.key, required this.card});
  final Map<String, dynamic> card;

  @override
  State<RecommendationCard> createState() => _RecommendationCardState();
}

class _RecommendationCardState extends State<RecommendationCard> {
  bool _details = false;

  @override
  Widget build(BuildContext context) {
    final card = widget.card;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(18),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(card['title'] as String,
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(card['message'] as String),
          const SizedBox(height: 12),
          const Text('Why this may help',
              style: TextStyle(fontWeight: FontWeight.w600)),
          Text(card['justification'] as String),
          if (card['requires_confirmation'] == true) ...[
            const SizedBox(height: 10),
            const Text('This situation still needs your confirmation.',
                style: TextStyle(color: Color(0xFF866630))),
          ],
          if (card['clarification_question'] != null) ...[
            const SizedBox(height: 8),
            Text(card['clarification_question'] as String,
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ],
          if (_details) ...[
            const SizedBox(height: 12),
            Text(card['description'] as String),
            const SizedBox(height: 8),
            const Text(
                'A suggestion only. No service has been ordered or action taken.',
                style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
          ],
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: () => setState(() => _details = !_details),
              // Fixed UI action: generated text cannot navigate or execute.
              child: Text(_details ? 'Hide details' : 'View details'),
            ),
          ),
        ]),
      ),
    );
  }
}
