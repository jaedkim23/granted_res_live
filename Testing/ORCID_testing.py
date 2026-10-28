import sys
import os
import pandas as pd
from sqlalchemy import text, bindparam
from config.settings import get_sqlalchemy_engine

testing_file = "ground_truth - names.csv"
# testing_file = "ground_truth - 200.csv"

df = pd.read_csv(testing_file)

# df = df[['res_id', 'first_name', 'last_name', 'author_name', 'crossref', 'orcid',
#        'orcid + crossref', 'orcid_no_affiliation', 'orcid_no_last_name',
#        'Correct ORCID ID', 'Comment']]


### Correct res_id value in eval set
engine = get_sqlalchemy_engine()
emp_tbl = pd.read_sql("SELECT res_id, first_name, last_name FROM emp_tbl", engine)

emp_tbl['first_name_lower'] = emp_tbl['first_name'].str.strip().str.lower()
emp_tbl['last_name_lower'] = emp_tbl['last_name'].str.strip().str.lower()

df['first_name_lower'] = df['first_name'].str.strip().str.lower()
df['last_name_lower'] = df['last_name'].str.strip().str.lower()

df = df.drop(columns='res_id').merge(
    emp_tbl[['res_id', 'first_name_lower', 'last_name_lower']],
    on=['first_name_lower', 'last_name_lower'],
    how='left'
).drop(columns=['first_name_lower', 'last_name_lower'])

df = df[df['Correct ORCID ID'].notna()]
df_sub1 = df[df['Correct ORCID ID'].notna() & (df['Correct ORCID ID'].str.strip() != '')]
# df_sub = df[df['Correct ORCID ID'].notna() & (df['Correct ORCID ID'].str.strip() != '')]
df_sub = df_sub1[df_sub1['res_id'].notna() & (df_sub1['res_id'] != 0)]

correct_orcids = df_sub['Correct ORCID ID'].str.strip().unique().tolist()

### Testing against CSV file
ORCID_OUT = "api/ORCID_results.csv"
orcid_df = pd.read_csv(ORCID_OUT)

emp_matches = orcid_df[orcid_df['res_id'].isin(df_sub['res_id'].tolist())]

valid_orcids = emp_matches.loc[emp_matches['res_id'].notna(), 'best_orcid'].unique()
df_sub = df_sub[df_sub['Correct ORCID ID'].str.strip().isin(valid_orcids)]
unmatched_orcids = [o for o in correct_orcids if o not in emp_matches['best_orcid'].values]

print("Number of valid ORCIDs: ", len(correct_orcids))
print("Number of matches: ", df_sub.shape[0])
print("Unmatched ORCIDs: ", unmatched_orcids)
emp_matches[emp_matches['best_orcid'].isin(unmatched_orcids)]
# emp_matches.to_csv("last_ver.csv")

### Testing against DB record
engine = get_sqlalchemy_engine()
query = text("SELECT * FROM emp_id_tbl WHERE orcid IN :orcids").bindparams(
    bindparam('orcids', expanding=True)
)
emp_matches = pd.read_sql(query, engine, params={"orcids": correct_orcids})

valid_orcids = emp_matches.loc[emp_matches['emp_id_id'].notna(), 'orcid'].unique()
df_sub = df_sub[df_sub['Correct ORCID ID'].str.strip().isin(valid_orcids)]
unmatched_orcids = [o for o in correct_orcids if o not in emp_matches['orcid'].values]

print("Number of valid ORCIDs: ", len(correct_orcids))
print("Number of matches: ", df_sub.shape[0])
print("Unmatched ORCIDs: ", unmatched_orcids)

print(emp_matches[emp_matches['orcid'].isin(unmatched_orcids)])

df_sub1[~df_sub1['Correct ORCID ID'].isin(valid_orcids)]