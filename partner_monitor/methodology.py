"""Human-readable methodology generated from the scoring configuration."""
def rows(rules):
    labels={'sanctions':'Confirmed applicable sanctions','cartel':'Cartel event','court_dispute':'Court dispute',
        'other_negative':'Other adverse event or one year of negative equity',
        'two_year_negative_equity':'Two consecutive years of negative equity (replaces one-year penalty)',
        'persistent_negative_equity':'Three consecutive years of negative equity (replaces shorter duration)',
        'large_annual_loss':'Annual loss above EUR '+format(rules['financial']['loss_threshold_eur'],',')+
            ' within the latest '+str(rules['financial']['loss_lookback_years'])+' reporting years (once)'}
    return [{'Rule':labels.get(key,key.replace('_',' ')),'Points deducted':weight} for key,weight in rules['weights'].items()]

def summary(rules):
    return ('Starting score: '+str(rules['start_score'])+'. Minimum: '+str(rules['minimum_score'])+
        '. Not recommended below '+str(rules['not_recommended_below'])+'. Equity duration penalties do not stack. '
        'The annual-loss penalty is additional. Missing checks make recommendations provisional; they do not change the score.')
