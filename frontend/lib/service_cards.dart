import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

Uri? officialDestination(Map<String, dynamic> service) {
  if (service['destination_available'] != true ||
      service['destination_status'] != 'verified' ||
      service['verification_status'] != 'official_source') {
    return null;
  }
  final raw = service['destination_url'];
  if (raw is! String || raw.contains(RegExp(r'\s|\\'))) return null;
  final uri = Uri.tryParse(raw);
  const hosts = {
    'www.kbc.be',
    'www.kbc.com',
    'www.kbcbrussels.be',
    'startit-x.com'
  };
  if (uri == null ||
      uri.scheme != 'https' ||
      !hosts.contains(uri.host) ||
      uri.userInfo.isNotEmpty ||
      (uri.hasPort && uri.port != 443) ||
      uri.hasQuery ||
      uri.hasFragment) {
    return null;
  }
  return uri;
}

typedef ServiceDismiss = Future<bool> Function(String id);
typedef ServiceLauncher = Future<bool> Function(Uri uri);

/// Catalogue-only destinations. No generated link, account data or URL parameters.
class ServiceCards extends StatefulWidget {
  const ServiceCards({
    super.key,
    required this.services,
    this.dismiss,
    this.launch,
    this.excluded = const [],
  });
  final List<dynamic> services;
  final ServiceDismiss? dismiss;
  final ServiceLauncher? launch;
  final List<dynamic> excluded;

  @override
  State<ServiceCards> createState() => _ServiceCardsState();
}

class _ServiceCardsState extends State<ServiceCards> {
  final Set<String> _hidden = {};
  final Set<String> _saving = {};

  Future<void> _open(Map<String, dynamic> service) async {
    final uri = officialDestination(service);
    if (uri == null) return;
    bool opened = false;
    try {
      opened = await (widget.launch?.call(uri) ??
          launchUrl(uri,
              mode: LaunchMode.externalApplication,
              webOnlyWindowName: '_blank'));
    } catch (_) {
      opened = false;
    }
    if (!opened && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content:
              Text('Unable to open the official page. Please try again.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final visible = widget.services
        .whereType<Map>()
        .map((s) => Map<String, dynamic>.from(s))
        .where((s) =>
            !_hidden.contains(s['id']) && !widget.excluded.contains(s['id']))
        .toList();
    if (visible.isEmpty) return const SizedBox.shrink();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const Padding(
          padding: EdgeInsets.only(top: 12, bottom: 6),
          child: Text('Related ecosystem services',
              style: TextStyle(fontWeight: FontWeight.w600))),
      const Text(
          'Selected from the verified catalogue. Optional external information; no banking action is performed.',
          style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
      for (final service in visible)
        Card(
            key: ValueKey('service-${service['id']}'),
            margin: const EdgeInsets.symmetric(vertical: 6),
            child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text('${service['name']}',
                          style: Theme.of(context).textTheme.titleSmall),
                      Text('${service['provider']} · ${service['ecosystem']}',
                          style: const TextStyle(
                              fontSize: 12, color: Colors.blueGrey)),
                      const SizedBox(height: 6),
                      Text(
                          '${service['why_recommended'] ?? service['description']}'),
                      if (service['requires_confirmation'] == true)
                        const Text(
                            'Explore only if this project applies to you; it still needs confirmation.'),
                      if (officialDestination(service) == null)
                        const Text(
                            'A direct official destination is not yet verified.')
                      else
                        Align(
                            alignment: Alignment.centerLeft,
                            child: TextButton.icon(
                                onPressed: () => _open(service),
                                icon: const Icon(Icons.open_in_new, size: 18),
                                label: const Text('Learn more'))),
                      if (widget.dismiss != null)
                        Align(
                            alignment: Alignment.centerLeft,
                            child: TextButton(
                                onPressed: _saving.contains(service['id'])
                                    ? null
                                    : () async {
                                        final id = service['id'] as String;
                                        setState(() => _saving.add(id));
                                        try {
                                          if (await widget.dismiss!(id) &&
                                              mounted) {
                                            setState(() => _hidden.add(id));
                                          }
                                        } finally {
                                          if (mounted) {
                                            setState(() => _saving.remove(id));
                                          }
                                        }
                                      },
                                child: const Text('Hide this service'))),
                    ]))),
    ]);
  }
}
