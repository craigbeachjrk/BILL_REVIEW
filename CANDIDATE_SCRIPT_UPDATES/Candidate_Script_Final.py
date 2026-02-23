from cmath import isnan
from math import nan
import pandas as pd
import xlwings as xw
from scipy.stats import zscore
import numpy as np
from numpy import nan
import numpy as np
from statistics import mean 
from datetime import date
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time
import glob
import os.path
from tkinter import *



################################Scrape from MTLCHILD###################
# # driver = webdriver.Chrome(service=Service(ChromeDriverManager(version='114.0.5735.16').install()))
# driver = webdriver.Chrome(service=Service())
# driver.maximize_window()
# driver.get("https://te.mtlchild.com/login.html")
# print("Application title is ", driver.title)
# print("Application url is ", driver.current_url)



# #Email or ID is id = "email"
# #Password is id = "password"

# user = driver.find_element(By.ID,"email")
# passw = driver.find_element(By.ID,"password")

# time.sleep(2)
# user.click()
# user.send_keys("mlippman@jrk.com")
# time.sleep(2)
# passw.click()
# passw.send_keys("MattyIce18")

# time.sleep(2)
# sign_in = driver.find_element(By.XPATH, '//*[@id="form-auth"]/div[4]/div/button')
# sign_in.click()

# time.sleep(5)

# global_button = driver.find_element(By.XPATH, '/html/body/main/ui-view/page-top/div/a[2]')
# global_button.click()

# time.sleep(2)

# download_button = driver.find_element(By.XPATH, '/html/body/main/ui-view/div/div/div/div/div/div/div/div[2]/div/div/div/div[1]/div/div[1]/div/a')
# download_button.click()

# time.sleep(2)
# driver.quit()


######################WHere to find FIle from MTLDCHILD#####################################################################################
def callback():
    global val
    val = E1.get()
    print(val)
    top.destroy()



top = Tk()
L1 = Label(top, text="Enter first initial of First Name + Last Name - (All Lowercase) - i.e. mlaitman")
L1.pack(pady=20)
L2 = Label(top, text = 'Close Window After Pressing Submit')
L2.pack(pady=20)
E1 = Entry(top, bd = 5)
E1.pack(pady=20)
care_about = E1.get()
MyButton1 = Button(top, text="Submit", width=10, command=callback).pack()
top.mainloop()


folder_path = "C:\\Users" + "\\" + val + "\\" + "Downloads" 
file_type = r'\*csv'
files = glob.glob(folder_path + file_type)
max_file = max(files, key=os.path.getctime)

print(max_file)


download_df = pd.read_csv(max_file)
###########################End SCrape###################################
path = r"H:\Business_Intelligence\2. CONFIDENTIAL_PROJECTS\Control Book.xlsx"

app = xw.App()
wkbk = xw.Book(path)

#Replace Old Download with New Download
wkbk.sheets['Download'].clear()
wkbk.sheets['Download']["A1"].options(pd.DataFrame, header=1, index=False, expand='table').value = download_df
wkbk.save()
wkbk.close()


wkbk = xw.Book(path,read_only=True)

sheet = wkbk.sheets['Test Mapping']
negotiation = wkbk.sheets['Negotiation']




map_df = sheet.range('B2').options(pd.DataFrame, 
                            index = False,
                             expand='table').value



download = pd.read_excel(r"H:\Business_Intelligence\2. CONFIDENTIAL_PROJECTS\Control Book.xlsx", sheet_name="Download")
download_copy = download.copy()

for rowIndex, row in map_df.iterrows(): #iterate over rows
    old_name = row[0]
#print(old_name)
    new_name = row[1]
#print(new_name)
    if new_name == None:
        pass
    else:
        download_copy.rename(columns = {old_name:new_name}, inplace = True)

##The above Code should be used to replace the names from the download sheet with the names in the Test mapping sheet
## Which can be found in the "For Mike" workbook. The output from the code above is the file called download copy
#At this point, download copy is equivalent to the paste tab in the "for mike" workbook
#Next step is creating a dataframe with the zscores of what we are looking for 



#Make one column for interview

#Get rid of interview data that isnt "overall" or michigan
index1 = download_copy.columns.get_loc("Aaron Cohen") #First Name of Interviewer
index2 = download_copy.columns.get_loc("Will Myers")+1 #Last Name of Interviewer + 1 
download_copy.drop(download_copy.columns[index1:index2],axis=1, inplace=True)


intaverage_df = pd.DataFrame(data = download_copy.iloc[:,0:2])
intoverall_df = pd.DataFrame(data = download_copy.iloc[:,0:2])
for colname in download_copy.columns:
	if "Interview" in colname:
		intaverage_df[colname] = download_copy[colname]
	elif "Overall" in colname:
		intoverall_df[colname] = download_copy[colname]


#Code below computes all of the mean scores for each interviewer and stores in "all_means" dictionary
all_means = {}
for i in intaverage_df.columns:
    if i in ['Name', 'E-mail']:
        pass
    else:   
        mean_score =  intaverage_df[i].mean()
        all_means[i] = mean_score


#This section creates the expected scores for the averages

#drop first column for analysis
intave_nofirst = intaverage_df.iloc[:,2:]

list_of_scores = []
list_of_interviewer_scores = []
for index, row in intave_nofirst.iterrows():
    col_index = 0
    interviewer_scores = []
    list_of_interviewer_scores.append(interviewer_scores)
    list_of_scores.append(row.mean())
    for item in row:
        if pd.isna(item) == True:
            print("Null Value")
            interviewer_scores.append(nan)
            col_index +=1 
        else: 
            col_name = intave_nofirst.columns[col_index]
            print(col_name)
            print(all_means[col_name])
            interviewer_scores.append(all_means[col_name])
            col_index +=1 
            

expected_scores = []
for row_scores in list_of_interviewer_scores:
    expected_scores.append(np.nanmean(row_scores))
    
#intaverage_df['Raw Score'] = list_of_scores
intaverage_df.insert(2,"Raw Score", list_of_scores)
#intaverage_df['Expected Score'] = expected_scores
intaverage_df.insert(3,"Expected Score", expected_scores)
#intaverage_df['% Above Expected'] = (intaverage_df['Raw Score'] - intaverage_df['Expected Score'] )/intaverage_df['Expected Score']
intaverage_df.insert(4, "% Above Expected", list((intaverage_df['Raw Score'] - intaverage_df['Expected Score'] )/intaverage_df['Expected Score']))
#intaverage_df["Rank"] = intaverage_df['% Above Expected'].rank(ascending = False)
intaverage_df.insert(5, "Rank", list(intaverage_df['% Above Expected'].rank(ascending = False)))
num_of_vals = len(intaverage_df['Raw Score'][pd.isna(intaverage_df['Raw Score']) == False])
#intaverage_df['Percentile'] = (1-(intaverage_df['Rank']/num_of_vals))
intaverage_df.insert(6, "Percentile", list(1-(intaverage_df['Rank']/num_of_vals)))

###DUplicating work above except for overall interview score 


all_means2 = {}
for i in intoverall_df.columns:
    if i in ['Name', 'E-mail']:
        pass
    else:   
        mean_score =  intoverall_df[i].mean()
        all_means2[i] = mean_score


#This section creates the expected scores for the average Interviews

#drop first column for analysis
intover_nofirst = intoverall_df.iloc[:,2:]

list_of_scores2 = []
list_of_interviewer_scores2 = []
for index, row in intover_nofirst.iterrows():
    col_index = 0
    interviewer_scores2 = []
    list_of_interviewer_scores2.append(interviewer_scores2)
    list_of_scores2.append(row.mean())
    for item in row:
        if pd.isna(item) == True:
            print("Null Value")
            interviewer_scores2.append(nan)
            col_index +=1 
        else: 
            col_name = intover_nofirst.columns[col_index]
            print(col_name)
            print(all_means2[col_name])
            interviewer_scores2.append(all_means2[col_name])
            col_index +=1 
            

expected_scores2 = []
for row_scores in list_of_interviewer_scores2:
    expected_scores2.append(np.nanmean(row_scores))
    
#intoverall_df['Raw Score'] = list_of_scores2
intoverall_df.insert(2, "Raw Score", list_of_scores2)
intoverall_df.insert(3, "Expected Score", expected_scores2)
#intoverall_df['Expected Score'] = expected_scores2

#intoverall_df['% Above Expected'] = (intoverall_df['Raw Score'] - intoverall_df['Expected Score'])/intoverall_df['Expected Score']
list_above_exp2 = (intoverall_df['Raw Score'] - intoverall_df['Expected Score'])/intoverall_df['Expected Score']
intoverall_df.insert(4, "% Above Expected", list_above_exp2)

intoverall_df.insert(5, "Rank", list(intoverall_df['% Above Expected'].rank(ascending = False)))
#intoverall_df["Rank"] = intoverall_df['% Above Expected'].rank(ascending = False)

num_of_vals2 = len(intoverall_df['Raw Score'][pd.isna(intoverall_df['Raw Score']) == False])
#intoverall_df['Percentile'] = (1-(intoverall_df['Rank']/num_of_vals2))
intoverall_df.insert(6, "Percentile", list(1-(intoverall_df['Rank']/num_of_vals2)))


###End to Duplication ###########################
#We have both Interview Sheets ready to go 
# They are stored in intoverall_df and intaverage_df
#Make A column for Influence & Pre-Suasion Composite

index1c = download_copy.columns.get_loc("Aaron Cohen Interview") #First Name of Interviewer
index2c = download_copy.columns.get_loc("Tom Manzo Overall")+1 #Last Name of Interviewer + 1
final_df = download_copy.drop(download_copy.iloc[:, index1c:index2c], axis=1)


#Columns that need to be added
final_df['Influence & Pre-Suasion Composite'] = final_df['Influence Quotient'] + final_df['Pre-Suasion']
final_df['Structured Interview - Average'] = intaverage_df['% Above Expected']
final_df['Structured Interview - Overall'] = intoverall_df['% Above Expected']
#Remove Interview Columns 
z_df = pd.DataFrame()

for col in final_df.columns:
    if col not in ['Name', 'E-mail', 'Position']:
        # Create a series with the same index as final_df
        z_scores = pd.Series(index=final_df.index, dtype=float)
        # Calculate z-scores only for non-null values
        non_null_mask = final_df[col].notna()
        if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
            z_scores[non_null_mask] = zscore(final_df[col][non_null_mask].astype(float))
        z_df[col] = z_scores
    else:
        z_df[col] = final_df[col]



 
##Z_df has all z scores (Calculated by using in column mean)
##Next step is to make all of the designated Z-score dataframes
weight_sheet = wkbk.sheets['Test Weights']

keys_range = weight_sheet.range('B3:B21').value
values_range = weight_sheet.range('C3:C21').value

weights_dict = dict(zip(keys_range,values_range))


cust_values_range = weight_sheet.range('D3:D21').value
weights_dict_cust = dict(zip(keys_range,cust_values_range))



############Grab Z-scores for Each group###################################
z_groupings= pd.read_excel(r"H:\Business_Intelligence\2. CONFIDENTIAL_PROJECTS\Control Book.xlsx", sheet_name="Z-Scores for Each Group", usecols='B:H',skiprows = 1)



#Corporate Non Professsional 
##nonpro_z_df has all z scores for non-Pro grouping (Calculated by using in column mean)
nonpro_filter = ['Corporate | Corporate - Non Professional | IT - Systems Administrator',
'Corporate | Corporate - Non Professional | IT - Systems Analyst',
'Corporate | Corporate - Non Professional | Development - Analyst']
nonpro_filter = list(z_groupings['Corporate Non-Professional'])
nonpro_df  = final_df[final_df['Position'].isin(nonpro_filter)] 
nonpro_z_df = pd.DataFrame()

for col in nonpro_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=nonpro_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = nonpro_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(nonpro_df[col][non_null_mask].astype(float))
            nonpro_z_df[col] = z_scores
        else:
            nonpro_z_df[col] = nonpro_df[col]
    except ValueError:
        print(col, "contains Null values")
        nonpro_z_df[col] = nonpro_df[col]

nonpro_z_df = nonpro_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising',
       'Technical Aptitude Test Timed', 'Technical Aptitude Test Untimed', 
       'Technical Aptitude Test Timed/Min', 'Technical Aptitude Test Untimed/Min'])

blankscore_sheet = wkbk.sheets['Blank Score Treatment']


blankscore_dict = blankscore_sheet.range('B2').options(dict, 
                            index = False,
                             expand='table').value

for col_name in nonpro_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            nonpro_z_df[col_name][pd.isna(nonpro_z_df[col_name])] = nan
        else:
            nonpro_z_df[col_name][pd.isna(nonpro_z_df[col_name])] = blankscore_dict[col_name]


nonpro_z_df2 = nonpro_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_nonpro = []
for index, row in nonpro_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_nonpro.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = nonpro_z_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_nonpro.append(combined_score)

nonpro_z_df['Score'] = final_scores_nonpro
nonpro_z_df['Overall Rank'] = nonpro_z_df['Score'].rank(ascending = False)
num_of_vals_nonpro = len(nonpro_z_df['Score'][pd.isna(nonpro_z_df['Score']) == False])
nonpro_z_df['Overall Percentile'] = (1-(nonpro_z_df['Overall Rank']/num_of_vals_nonpro))

try:
    # Create a series with the same index as nonpro_z_df for Final Z-Score
    z_scores = pd.Series(index=nonpro_z_df.index, dtype=float)
    # Calculate z-scores only for non-null values
    non_null_mask = nonpro_z_df['Score'].notna()
    if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
        z_scores[non_null_mask] = zscore(nonpro_z_df['Score'][non_null_mask].astype(float))
    nonpro_z_df['Final Z-Score'] = z_scores
except:
    nonpro_z_df['Final Z-Score'] = nan

raw_scores_nonpro = download[download['Position'].isin(nonpro_filter)]
raw_scores_nonpro = raw_scores_nonpro[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'Technical Aptitude Test - Timed', 'Technical Aptitude Test - Untimed','CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed2 = thresh_sheet.range('F8').value
hip_untimed2 = thresh_sheet.range('F9').value
tech_ap_timed2 = thresh_sheet.range('F12').value
tech_ap_untimed2 = thresh_sheet.range('F13').value
crt_weight2 = thresh_sheet.range('F25').value
final_hip_thresh2 = thresh_sheet.range('F3').value
final_tech_thresh2 = thresh_sheet.range('F4').value
final_crt_thresh2 = thresh_sheet.range('F5').value

raw_scores_nonpro['Weighted Hippogriff'] = (raw_scores_nonpro['Hippogriff - Timed'] * hip_timed2) + (raw_scores_nonpro['Hippogriff - Untimed'] * hip_untimed2)
raw_scores_nonpro['Weighted CRT'] = (raw_scores_nonpro['CRT Untimed'] * crt_weight2)

raw_scores_nonpro['Passed'] = np.where( (raw_scores_nonpro['Weighted Hippogriff'] >= final_hip_thresh2) 
& (raw_scores_nonpro['Weighted CRT'] >= final_crt_thresh2), True, False)


raw_scores_nonpro = raw_scores_nonpro[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted CRT', 'Passed']]
nonpro_z_df = pd.merge(nonpro_z_df , raw_scores_nonpro, on = ['Name', 'E-mail', 'Position'], how= "left")

nonpro_avg_pay = float(negotiation.range('C5').value)
nonpro_upto = negotiation.range('D5').value
nonpro_z_df['Average Pay'] = nonpro_avg_pay
nonpro_z_df['Value Added'] = nonpro_avg_pay * nonpro_z_df['Final Z-Score'] * nonpro_upto
nonpro_z_df['Pay Up To'] = nonpro_z_df['Average Pay'] + nonpro_z_df['Value Added']

##Corporate Professional
pro_filter = ['Corporate | Corporate - Professional | Multifamily Asset Management Analyst',
'Corporate | Corporate - Professional | Hotel Asset Management Analyst',
'Corporate | Corporate - Professional | Multifamily Acquisitions Analyst',
'Corporate | Corporate - Professional | Hotel Acquisitions Analyst',
'Corporate | Corporate - Professional | Development Associate Project Manager',
'Corporate | Corporate - Professional | Multifamily Asset Manager',
'Corporate | Corporate - Professional | Hotel Asset Manager',
'Corporate | Corporate - Professional | Multifamily Acquisitions Associate',
'Corporate | Corporate - Professional | Development Vice President Project Manager']
pro_filter =  list(z_groupings['Professional Score'])
pro_df  = final_df[final_df['Position'].isin(pro_filter)] 
pro_z_df = pd.DataFrame()

for col in pro_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=pro_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = pro_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(pro_df[col][non_null_mask].astype(float))
            pro_z_df[col] = z_scores
        else:
            pro_z_df[col] = pro_df[col]
    except ValueError:
        print(col, "contains Null values")
        pro_z_df[col] = pro_df[col]

pro_z_df = pro_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising'])

for col_name in pro_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            pro_z_df[col_name][pd.isna(pro_z_df[col_name])] = nan
        else:
            pro_z_df[col_name][pd.isna(pro_z_df[col_name])] = blankscore_dict[col_name]


pro_z_df2 = pro_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_pro = []
for index, row in pro_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_pro.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = pro_z_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_pro.append(combined_score)

pro_z_df['Score'] = final_scores_pro
pro_z_df['Overall Rank'] = pro_z_df['Score'].rank(ascending = False)
num_of_vals_pro = len(pro_z_df['Score'][pd.isna(pro_z_df['Score']) == False])
pro_z_df['Overall Percentile'] = (1-(pro_z_df['Overall Rank']/num_of_vals_pro))
try:
    # Create a series with the same index as pro_z_df for Final Z-Score
    z_scores = pd.Series(index=pro_z_df.index, dtype=float)
    # Calculate z-scores only for non-null values
    non_null_mask = pro_z_df['Score'].notna()
    if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
        z_scores[non_null_mask] = zscore(pro_z_df['Score'][non_null_mask].astype(float))
    pro_z_df['Final Z-Score'] = z_scores
except:
    pro_z_df['Final Z-Score'] = nan

raw_scores_pro = download[download['Position'].isin(pro_filter)]
raw_scores_pro = raw_scores_pro[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'Technical Aptitude Test - Timed', 'Technical Aptitude Test - Untimed','CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed1 = thresh_sheet.range('D8').value
hip_untimed1 = thresh_sheet.range('D9').value
tech_ap_timed1 = thresh_sheet.range('D12').value
tech_ap_untimed1 = thresh_sheet.range('D13').value
crt_weight1 = thresh_sheet.range('D25').value
final_hip_thresh1 = thresh_sheet.range('D3').value
final_tech_thresh1 = thresh_sheet.range('D4').value
final_crt_thresh1 = thresh_sheet.range('D5').value

raw_scores_pro['Weighted Hippogriff'] = (raw_scores_pro['Hippogriff - Timed'] * hip_timed1) + (raw_scores_pro['Hippogriff - Untimed'] * hip_untimed1)
raw_scores_pro['Weighted Technical Aptitude'] = (raw_scores_pro['Technical Aptitude Test - Timed'] * tech_ap_timed1) + (raw_scores_pro['Technical Aptitude Test - Untimed'] * tech_ap_untimed1)
raw_scores_pro['Weighted CRT'] = (raw_scores_pro['CRT Untimed'] * crt_weight1)

raw_scores_pro['Passed'] = np.where( (raw_scores_pro['Weighted Hippogriff'] >= final_hip_thresh1) 
& (raw_scores_pro['Weighted Technical Aptitude'] >= final_tech_thresh1)
& (raw_scores_pro['Weighted CRT'] >= final_crt_thresh1), True, False)


raw_scores_pro = raw_scores_pro[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed']]
pro_z_df = pd.merge(pro_z_df , raw_scores_pro, on = ['Name', 'E-mail', 'Position'], how= "left")


pro_avg_pay = float(negotiation.range('C3').value)
pro_upto = negotiation.range('D3').value
pro_z_df['Average Pay'] = pro_avg_pay
pro_z_df['Value Added'] = pro_avg_pay * pro_z_df['Final Z-Score'] * pro_upto
pro_z_df['Pay Up To'] = pro_z_df['Average Pay'] + pro_z_df['Value Added']


#Corporate Misc
##misc_z_df has all z scores for misc grouping (Calculated by using in column mean)
misc_filter = ['Corporate | Corporate Misc. | Mail Room']
misc_filter = list(z_groupings['Corporate Misc.'])
misc_df = final_df[final_df['Position'].isin(misc_filter)]
misc_z_df = pd.DataFrame()

for col in misc_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=misc_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = misc_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(misc_df[col][non_null_mask].astype(float))
            misc_z_df[col] = z_scores
        else:
            misc_z_df[col] = misc_df[col]
    except ValueError:
        print(col, "contains Null values")
        misc_z_df[col] = misc_df[col]

misc_z_df = misc_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising','Technical Aptitude Test Timed', 'Technical Aptitude Test Untimed', 
       'Technical Aptitude Test Timed/Min', 'Technical Aptitude Test Untimed/Min'])

for col_name in misc_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            misc_z_df[col_name][pd.isna(misc_z_df[col_name])] = nan
        else:
            misc_z_df[col_name][pd.isna(misc_z_df[col_name])] = blankscore_dict[col_name]

misc_z_df2 = misc_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_misc = []
for index, row in misc_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_misc.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = misc_z_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_misc.append(combined_score)

misc_z_df['Score'] = final_scores_misc
misc_z_df['Overall Rank'] = misc_z_df['Score'].rank(ascending = False)



num_of_vals_misc = len(misc_z_df['Score'][pd.isna(misc_z_df['Score']) == False])
misc_z_df['Overall Percentile'] = (1-(misc_z_df['Overall Rank']/num_of_vals_misc))
try:
    misc_z_df['Final Z-Score'] = zscore(misc_z_df['Score'].astype(float).dropna())
except: 
    misc_z_df['Final Z-Score'] = nan

raw_scores_misc = download[download['Position'].isin(misc_filter)]
raw_scores_misc = raw_scores_misc[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'Technical Aptitude Test - Timed', 'Technical Aptitude Test - Untimed','CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed3 = thresh_sheet.range('G8').value
hip_untimed3 = thresh_sheet.range('G9').value
tech_ap_timed3 = thresh_sheet.range('G12').value
tech_ap_untimed3 = thresh_sheet.range('G13').value
crt_weight3 = thresh_sheet.range('G25').value
final_hip_thresh3 = thresh_sheet.range('G3').value
final_tech_thresh3 = thresh_sheet.range('G4').value
final_crt_thresh3 = thresh_sheet.range('G5').value

raw_scores_misc['Weighted Hippogriff'] = (raw_scores_misc['Hippogriff - Timed'] * hip_timed3) + (raw_scores_misc['Hippogriff - Untimed'] * hip_untimed3)
raw_scores_misc['Weighted CRT'] = (raw_scores_misc['CRT Untimed'] * crt_weight3)

raw_scores_misc['Passed'] = np.where( (raw_scores_misc['Weighted Hippogriff'] >= final_hip_thresh3) 
& (raw_scores_misc['Weighted CRT'] >= final_crt_thresh3), True, False)


raw_scores_misc = raw_scores_misc[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted CRT', 'Passed']]
misc_z_df = pd.merge(misc_z_df , raw_scores_misc, on = ['Name', 'E-mail', 'Position'], how= "left")

misc_avg_pay = float(negotiation.range('C6').value)
misc_upto = negotiation.range('D6').value
misc_z_df['Average Pay'] = misc_avg_pay
misc_z_df['Value Added'] = misc_avg_pay * misc_z_df['Final Z-Score'] * misc_upto
misc_z_df['Pay Up To'] = misc_z_df['Average Pay'] + misc_z_df['Value Added']

#Multifamily
##multi_z_df has all z scores for mutltifamily grouping (Calculated by using in column mean)
multi_filter = ['Multifamily | Office | Regional Manager',
                'Multifamily | Office | Manager']
multi_filter  = list(z_groupings['Multifamily'])
multi_df = final_df[final_df['Position'].isin(multi_filter)]
multi_z_df = pd.DataFrame()

for col in multi_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=multi_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = multi_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(multi_df[col][non_null_mask].astype(float))
            multi_z_df[col] = z_scores
        else:
            multi_z_df[col] = multi_df[col]
    except ValueError:
        print(col, "contains Null values")
        multi_z_df[col] = multi_df[col]

multi_z_df = multi_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising', 'Technical Aptitude Test Timed', 'Technical Aptitude Test Untimed', 
       'Technical Aptitude Test Timed/Min', 'Technical Aptitude Test Untimed/Min'])

for col_name in multi_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            multi_z_df[col_name][pd.isna(multi_z_df[col_name])] = nan
        else:
            multi_z_df[col_name][pd.isna(multi_z_df[col_name])] = blankscore_dict[col_name]

multi_z_df2 = multi_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_multi = []
for index, row in multi_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_multi.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = multi_z_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_multi.append(combined_score)

multi_z_df['Score'] = final_scores_multi
multi_z_df['Overall Rank'] = multi_z_df['Score'].rank(ascending = False)
multi_z_df['Overall Rank'] = multi_z_df['Overall Rank'].astype('Int64')

num_of_vals_multi = len(multi_z_df['Score'][pd.isna(multi_z_df['Score']) == False])
multi_z_df['Overall Percentile'] = (1-(multi_z_df['Overall Rank']/num_of_vals_multi))
try:
    multi_z_df['Final Z-Score'] = zscore(multi_z_df['Score'].astype(float).dropna())
except:
    multi_z_df['Final Z-Score'] = nan


raw_scores_multi = download[download['Position'].isin(multi_filter)]
raw_scores_multi = raw_scores_multi[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed4 = thresh_sheet.range('H8').value
hip_untimed4 = thresh_sheet.range('H9').value
tech_ap_timed4 = thresh_sheet.range('H12').value
tech_ap_untimed4 = thresh_sheet.range('H13').value
crt_weight4 = thresh_sheet.range('H25').value
final_hip_thresh4 = thresh_sheet.range('H3').value
final_tech_thresh4 = thresh_sheet.range('H4').value
final_crt_thresh4 = thresh_sheet.range('H5').value

raw_scores_multi['Weighted Hippogriff'] = (raw_scores_multi['Hippogriff - Timed'] * hip_timed4) + (raw_scores_multi['Hippogriff - Untimed'] * hip_untimed4)
raw_scores_multi['Weighted CRT'] = (raw_scores_multi['CRT Untimed'] * crt_weight4)

raw_scores_multi['Passed'] = np.where( (raw_scores_multi['Weighted Hippogriff'] >= final_hip_thresh4) 
& (raw_scores_multi['Weighted CRT'] >= final_crt_thresh4), True, False)


raw_scores_multi = raw_scores_multi[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted CRT', 'Passed']]
multi_z_df = pd.merge(multi_z_df , raw_scores_multi, on = ['Name', 'E-mail', 'Position'], how= "left")

multi_avg_pay = float(negotiation.range('C7').value)
multi_upto = negotiation.range('D7').value
multi_z_df['Average Pay'] = multi_avg_pay
multi_z_df['Value Added'] = multi_avg_pay * multi_z_df['Final Z-Score'] * multi_upto
multi_z_df['Pay Up To'] = multi_z_df['Average Pay'] + multi_z_df['Value Added']

###############################################################################################
#Executive
##exec_z_df has all z scores for mutltifamily grouping (Calculated by using in column mean)
exec_filter = ['Corporate | Executive | JRK Property Holdings CFO']
exec_filter  = list(z_groupings['Executive Score'])
exec_df = final_df[final_df['Position'].isin(exec_filter)]
exec_z_df = pd.DataFrame()

for col in exec_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=exec_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = exec_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(exec_df[col][non_null_mask].astype(float))
            exec_z_df[col] = z_scores
        else:
            exec_z_df[col] = exec_df[col]
    except ValueError:
        print(col, "contains Null values")
        exec_z_df[col] = exec_df[col]

exec_z_df = exec_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising'])

for col_name in exec_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            exec_z_df[col_name][pd.isna(exec_z_df[col_name])] = nan
        else:
            exec_z_df[col_name][pd.isna(exec_z_df[col_name])] = blankscore_dict[col_name]

exec_z_df2 = exec_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_exec = []
for index, row in exec_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_exec.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = exec_z_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_exec.append(combined_score)

exec_z_df['Score'] = final_scores_exec
exec_z_df['Overall Rank'] = exec_z_df['Score'].rank(ascending = False)


num_of_vals_exec = len(exec_z_df['Score'][pd.isna(exec_z_df['Score']) == False])
exec_z_df['Overall Percentile'] = (1-(exec_z_df['Overall Rank']/num_of_vals_exec))
try:
    # Create a series with the same index as exec_z_df for Final Z-Score
    z_scores = pd.Series(index=exec_z_df.index, dtype=float)
    # Calculate z-scores only for non-null values
    non_null_mask = exec_z_df['Score'].notna()
    if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
        z_scores[non_null_mask] = zscore(exec_z_df['Score'][non_null_mask].astype(float))
    exec_z_df['Final Z-Score'] = z_scores
except:
    exec_z_df['Final Z-Score'] = nan



raw_scores_exec = download[download['Position'].isin(exec_filter)]
raw_scores_exec = raw_scores_exec[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'Technical Aptitude Test - Timed', 'Technical Aptitude Test - Untimed','CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed5 = thresh_sheet.range('E8').value
hip_untimed5 = thresh_sheet.range('E9').value
tech_ap_timed5 = thresh_sheet.range('E12').value
tech_ap_untimed5 = thresh_sheet.range('E13').value
crt_weight5 = thresh_sheet.range('E25').value
final_hip_thresh5 = thresh_sheet.range('E3').value
final_tech_thresh5 = thresh_sheet.range('E4').value
final_crt_thresh5 = thresh_sheet.range('E5').value

raw_scores_exec['Weighted Hippogriff'] = (raw_scores_exec['Hippogriff - Timed'] * hip_timed5) + (raw_scores_exec['Hippogriff - Untimed'] * hip_untimed5)
raw_scores_exec['Weighted Technical Aptitude'] = (raw_scores_exec['Technical Aptitude Test - Timed'] * tech_ap_timed5) + (raw_scores_exec['Technical Aptitude Test - Untimed'] * tech_ap_untimed5)
raw_scores_exec['Weighted CRT'] = (raw_scores_exec['CRT Untimed'] * crt_weight5)

raw_scores_exec['Passed'] = np.where( (raw_scores_exec['Weighted Hippogriff'] >= final_hip_thresh5) 
& (raw_scores_exec['Weighted Technical Aptitude'] >= final_tech_thresh5)
& (raw_scores_exec['Weighted CRT'] >= final_crt_thresh5), True, False)


raw_scores_exec = raw_scores_exec[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed']]
exec_z_df = pd.merge(exec_z_df , raw_scores_exec, on = ['Name', 'E-mail', 'Position'], how= "left")


exec_avg_pay = float(negotiation.range('C4').value)
exec_upto = negotiation.range('D4').value
exec_z_df['Average Pay'] = exec_avg_pay
exec_z_df['Value Added'] = exec_avg_pay * exec_z_df['Final Z-Score'] * exec_upto
exec_z_df['Pay Up To'] = exec_z_df['Average Pay'] + exec_z_df['Value Added']

########################################################################################################







#Custom
##cust_z_df has all z scores for custom grouping (Calculated by using in column mean)
cust_filter = ['Corporate | Corporate - Professional | Multifamily Asset Management Analyst',
       'Corporate | Corporate - Professional | Hotel Asset Management Analyst',
       'Corporate | Corporate - Professional | Multifamily Acquisitions Analyst',
       'Corporate | Corporate - Professional | Hotel Acquisitions Analyst',
       'Corporate | Executive | JRK Property Holdings CFO',
       'Corporate | Corporate - Professional | Development Associate Project Manager',
       'Corporate | Corporate - Professional | Multifamily Asset Manager',
       'Corporate | Corporate - Professional | Hotel Asset Manager',
       'Corporate | Corporate - Professional | Multifamily Acquisitions Associate',
       'Corporate | Corporate - Professional | Development Vice President Project Manager']
cust_filter  = list(z_groupings['Custom'])

cust_df = final_df[final_df['Position'].isin(cust_filter)]

cust_z_df = pd.DataFrame()

for col in cust_df.columns:
    try:
        if col not in ['Name', 'E-mail', 'Position']:
            # Create a series with the same index as final_df
            z_scores = pd.Series(index=cust_df.index, dtype=float)
            # Calculate z-scores only for non-null values
            non_null_mask = cust_df[col].notna()
            if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
                z_scores[non_null_mask] = zscore(cust_df[col][non_null_mask].astype(float))
            cust_z_df[col] = z_scores
        else:
            cust_z_df[col] = cust_df[col]
    except ValueError:
        print(col, "contains Null values")
        cust_z_df[col] = cust_df[col]

cust_z_df = cust_z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising'])

for col_name in cust_z_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            cust_z_df[col_name][pd.isna(cust_z_df[col_name])] = nan
        else:
            cust_z_df[col_name][pd.isna(cust_z_df[col_name])] = blankscore_dict[col_name]

cust_z_df2 = cust_z_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores_cust = []
for index, row in cust_z_df2.iterrows():
    if True in pd.isna(row):
        final_scores_cust.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = cust_z_df2.columns[row_index]
            if 'Technical Aptitude' in col_name:
                continue 
            print(weights_dict[col_name])
            weighted_val = weights_dict_cust[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores_cust.append(combined_score)

cust_z_df['Score'] = final_scores_cust
cust_z_df['Overall Rank'] = cust_z_df['Score'].rank(ascending = False)


num_of_vals_cust = len(cust_z_df['Score'][pd.isna(cust_z_df['Score']) == False])
cust_z_df['Overall Percentile'] = (1-(cust_z_df['Overall Rank']/num_of_vals_cust))
try:
    # Create a series with the same index as cust_z_df for Final Z-Score
    z_scores = pd.Series(index=cust_z_df.index, dtype=float)
    # Calculate z-scores only for non-null values
    non_null_mask = cust_z_df['Score'].notna()
    if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
        z_scores[non_null_mask] = zscore(cust_z_df['Score'][non_null_mask].astype(float))
    cust_z_df['Final Z-Score'] = z_scores
except:
    cust_z_df['Final Z-Score'] = nan

raw_scores_cust = download[download['Position'].isin(cust_filter)]
raw_scores_cust = raw_scores_cust[['Name', 'E-mail', 'Position', 'Hippogriff - Timed', 'Hippogriff - Untimed',
'Technical Aptitude Test - Timed', 'Technical Aptitude Test - Untimed','CRT Untimed']]
thresh_sheet = wkbk.sheets['Thresholds']
hip_timed6 = thresh_sheet.range('I8').value
hip_untimed6 = thresh_sheet.range('I9').value
tech_ap_timed6 = thresh_sheet.range('I12').value
tech_ap_untimed6 = thresh_sheet.range('I13').value
crt_weight6 = thresh_sheet.range('I25').value
final_hip_thresh6 = thresh_sheet.range('I3').value
final_tech_thresh6 = thresh_sheet.range('I4').value
final_crt_thresh6 = thresh_sheet.range('I5').value

raw_scores_cust['Weighted Hippogriff'] = (raw_scores_cust['Hippogriff - Timed'] * hip_timed6) + (raw_scores_cust['Hippogriff - Untimed'] * hip_untimed6)
raw_scores_cust['Weighted Technical Aptitude'] = (raw_scores_cust['Technical Aptitude Test - Timed'] * tech_ap_timed6) + (raw_scores_cust['Technical Aptitude Test - Untimed'] * tech_ap_untimed6)
raw_scores_cust['Weighted CRT'] = (raw_scores_cust['CRT Untimed'] * crt_weight6)

raw_scores_cust['Passed'] = np.where( (raw_scores_cust['Weighted Hippogriff'] >= final_hip_thresh6) 
& (raw_scores_cust['Weighted Technical Aptitude'] >= final_tech_thresh6)
& (raw_scores_cust['Weighted CRT'] >= final_crt_thresh6), True, False)


raw_scores_cust = raw_scores_cust[['Name', 'E-mail', 'Position', 'Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed']]
cust_z_df = pd.merge(cust_z_df , raw_scores_cust, on = ['Name', 'E-mail', 'Position'], how= "left")



cust_avg_pay = float(negotiation.range('C8').value)
cust_upto = negotiation.range('D8').value
cust_z_df['Average Pay'] = cust_avg_pay
cust_z_df['Value Added'] = cust_avg_pay * cust_z_df['Final Z-Score'] * cust_upto
cust_z_df['Pay Up To'] = cust_z_df['Average Pay'] + cust_z_df['Value Added']

#Creating Summary Tab for Final Workbook


summary_df = z_df.drop(columns= ['AOT Belief Personification',
       'AOT Dogmatism', 'AOT Fact Resistance', 'AOT Liberalism', 'CRT 1-Timed',
       'CRT 1-Untimed', 'CRT 2-Timed', 'CRT 2-Untimed', 'CRT 3-Timed',
       'CRT 3-Untimed', 'CRT Timed', 'CRT Timed score/min', 'Influence Quotient', 'Pre-Suasion', 'Give Take-Giver', 'Give Take-Matcher', 'Give Take-Taker', 
       'Grit','Negotiation-Accommodating',
       'Negotiation-Avoiding', 'Negotiation-Collaborating',
       'Negotiation-Competing', 'Negotiation-Compromising'])

#Blank Score Treatment 
blankscore_sheet = wkbk.sheets['Blank Score Treatment']


blankscore_dict = blankscore_sheet.range('B2').options(dict, 
                            index = False,
                             expand='table').value

for col_name in summary_df.columns:
    if col_name in blankscore_dict.keys():
        if blankscore_dict[col_name] == 'nan':
            summary_df[col_name][pd.isna(summary_df[col_name])] = nan
        else:
            summary_df[col_name][pd.isna(summary_df[col_name])] = blankscore_dict[col_name]



summary_df2 = summary_df.drop(columns=['Name', 'E-mail', 'Position'])

final_scores = []
for index, row in summary_df2.iterrows():
    if True in pd.isna(row):
        final_scores.append(nan)
    else:
        row_index = 0
        combined_score = 0
        for val in row:
            col_name = summary_df2.columns[row_index]
            print(weights_dict[col_name])
            weighted_val = weights_dict[col_name] * val 
            combined_score += weighted_val
            row_index +=1
        final_scores.append(combined_score)

summary_df['Score'] = final_scores
summary_df['Overall Rank'] = summary_df['Score'].rank(ascending = False)
num_of_vals3 = len(summary_df['Score'][pd.isna(summary_df['Score']) == False])
summary_df['Overall Percentile'] = (1-(summary_df['Overall Rank']/num_of_vals3))

# Create a series with the same index as summary_df for Final Z-Score
z_scores = pd.Series(index=summary_df.index, dtype=float)
# Calculate z-scores only for non-null values
non_null_mask = summary_df['Score'].notna()
if non_null_mask.sum() > 1:  # Need at least 2 values to calculate z-score
    z_scores[non_null_mask] = zscore(summary_df['Score'][non_null_mask].astype(float))
summary_df['Final Z-Score'] = z_scores

##Dataframe Pre-Processing for excel 


summary_df = summary_df.reindex(columns=['Name', 'E-mail', 'Position','Overall Rank','Overall Percentile', 'Score', 'Final Z-Score',
   'Average Pay', 'Value Added', 'Pay Up To',  'Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Technical Aptitude Test Timed','Technical Aptitude Test Untimed', 'Technical Aptitude Test Timed/Min',
       'Technical Aptitude Test Untimed/Min', 'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min'])

exec_z_df = exec_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position', 'Overall Percentile', 'Score', 'Final Z-Score',
         'Average Pay', 'Value Added', 'Pay Up To', 'Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Technical Aptitude Test Timed','Technical Aptitude Test Untimed', 'Technical Aptitude Test Timed/Min',
       'Technical Aptitude Test Untimed/Min', 'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min', 'Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed'])
exec_z_df = exec_z_df.sort_values(by='Overall Rank')

nonpro_z_df = nonpro_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position','Overall Percentile', 'Score', 'Final Z-Score',
        'Average Pay', 'Value Added', 'Pay Up To', 'Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min', 'Weighted Hippogriff', 'Weighted CRT', 'Passed'])
nonpro_z_df = nonpro_z_df.sort_values(by='Overall Rank')

pro_z_df = pro_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position','Overall Percentile', 'Score', 'Final Z-Score',
          'Average Pay', 'Value Added', 'Pay Up To','Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Technical Aptitude Test Timed','Technical Aptitude Test Untimed', 'Technical Aptitude Test Timed/Min',
       'Technical Aptitude Test Untimed/Min', 'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min', 'Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed'])
pro_z_df = pro_z_df.sort_values(by='Overall Rank')

multi_z_df = multi_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position','Overall Percentile', 'Score', 'Final Z-Score',
        'Average Pay', 'Value Added', 'Pay Up To','Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
       'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min', 'Weighted Hippogriff', 'Weighted CRT', 'Passed'])
multi_z_df = multi_z_df.sort_values(by='Overall Rank')

misc_z_df = misc_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position','Overall Percentile', 'Score', 'Final Z-Score',
         'Average Pay', 'Value Added', 'Pay Up To','Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min', 'Weighted Hippogriff', 'Weighted CRT', 'Passed'])
misc_z_df = misc_z_df.sort_values(by='Overall Rank')

cust_z_df = cust_z_df.reindex(columns=['Overall Rank','Name', 'E-mail', 'Position','Overall Percentile', 'Score', 'Final Z-Score',
          'Average Pay', 'Value Added', 'Pay Up To','Hippogriff Timed', 'Hippogriff Untimed', 'Hippogriff Timed/Min', 'Hippogriff Untimed/Min',
        'Technical Aptitude Test Timed','Technical Aptitude Test Untimed', 'Technical Aptitude Test Timed/Min',
       'Technical Aptitude Test Untimed/Min', 'Structured Interview - Average',
       'Structured Interview - Overall', 'Integrity', 'IPIP-NEO Conciscientiousness', 'IPIP-NEO Openness to Experience',
       'Influence & Pre-Suasion Composite', 'IPIP-NEO Extraversion', 'IPIP-NEO Agreeableness', 
       'IPIP-NEO Neuroticism','CRT', 'CRT/Min','Weighted Hippogriff', 'Weighted Technical Aptitude', 'Weighted CRT', 'Passed'])
cust_z_df = cust_z_df.sort_values(by='Overall Rank')
'''
intaverage_df = intaverage_df.reindex(columns=['Name', 'E-mail',  'Raw Score',
       'Expected Score', '% Above Expected', 'Rank', 'Percentile','Aaron Cohen Interview', 'Alex Shaftal Interview',
       'Ari Bender Interview', 'Bobby Lee Interview', 'Chong Yi Interview',
       'Chris Murray Interview', 'Danny Lippman Interview',
       'George Nausha Interview', 'Jake Rucker Interview',
       'James Bloomingdale Interview', 'James Broyer Interview',
       'Jay Schulman Interview', 'John Park Interview', 'Josh Park Interview',
       'Josiah Eberhart Interview', 'Kristie Tromp Interview',
       'Lawrence Baeck Interview', 'Matt Fontaine Interview',
       'Matt Lippman Interview', 'Matt Sussman Interview',
       'Max McCoy Interview', 'Nick Lejejs Interview',
       'Rob Harrington Interview', 'Tom Manzo Interview',
       'Wesley Rivelle Interview', 'Will Myers Interview'])'''
intaverage_df = intaverage_df.sort_values(by = 'Rank')
intaverage_df.reset_index(drop=True, inplace=True)


'''intoverall_df = intoverall_df.reindex(columns = ['Name', 'E-mail',  'Raw Score', 'Expected Score', '% Above Expected',
       'Rank', 'Percentile', 'Ari Bender Overall', 'Chong Yi Overall', 'George Nausha Overall',
       'John Park Overall', 'Josh Park Overall', '', 'Kristie Tromp Overall',
       'Lawrence Baeck Overall', 'Matt Sussman Overall', 'Max McCoy Overall',
       'Nick Lejejs Overall', 'Rob Harrington Overall', 'Tom Manzo Overall',
       'Will Myers Overall'])'''
intoverall_df = intoverall_df.sort_values(by = 'Rank')
intoverall_df.reset_index(drop=True, inplace=True)



file_name = 'Candidate Final Output - ' + str(date.today()) +  '.xlsx'


final_path = r"H:\Business_Intelligence\2. CONFIDENTIAL_PROJECTS\\" +  str(file_name)

download.to_excel(final_path, freeze_panes=(1, 100))
final_wkbk = xw.Book(final_path)



final_wkbk.sheets[0].name = "Download"
final_wkbk.sheets[0].autofit('c')


final_wkbk.sheets.add('Z Scores', after='Download' ) 
final_wkbk.sheets['Z Scores']["B2"].options(pd.DataFrame, header=1, index=True, expand='table').value = z_df.round(3)

final_wkbk.sheets.add('Interview - Average', after='Z Scores') 
final_wkbk.sheets['Interview - Average']["B2"].options(pd.DataFrame, header=1, index=True, expand='table').value = intaverage_df.round(3)

final_wkbk.sheets.add('Interview - Overall', after = 'Interview - Average') 
final_wkbk.sheets['Interview - Overall']["B2"].options(pd.DataFrame, header=1, index=True, expand='table').value = intoverall_df.round(3)

final_wkbk.sheets.add('Corporate Non - Pro', before = 'Interview - Average') 
final_wkbk.sheets['Corporate Non - Pro']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = nonpro_z_df.round(3)

final_wkbk.sheets.add('Corporate Pro', before = 'Corporate Non - Pro') 
final_wkbk.sheets['Corporate Pro']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = pro_z_df.round(3)

final_wkbk.sheets.add('Executives', before = 'Corporate Non - Pro') 
final_wkbk.sheets['Executives']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = exec_z_df.round(3)


final_wkbk.sheets.add('Corporate Misc', after = 'Corporate Non - Pro') 
final_wkbk.sheets['Corporate Misc']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = misc_z_df.round(3)

final_wkbk.sheets.add('Multifamily', after = 'Corporate Misc') 
final_wkbk.sheets['Multifamily']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = multi_z_df.round(3)


final_wkbk.sheets.add('Custom', after = 'Multifamily') 
final_wkbk.sheets['Custom']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = cust_z_df.round(3)


final_wkbk.sheets.add('Summary', after='Custom')
final_wkbk.sheets['Summary']["B2"].options(pd.DataFrame, header=1, index=False, expand='table').value = summary_df.round(3)




#Goes through Each Dataframe and changes color of rows that pass threshold
for work in final_wkbk.sheets:
    top_row = work.range('B2').expand('right')
    if 'Passed' in top_row.value:
        for i in top_row:
            if work.range(i.address).value == 'Passed':
                pass_row = work.range(i.address).expand('down')
                for val in pass_row:
                    if work.range(val.address).value == True:
                        print('TRUEEEEEEEEEEEE')
                        #print(val.address)
                        print(work.range(val.address).value)
                        print()
                        val.row
                        rowindex1 = '$B' + str(val.row)
                        #print(rowindex1)
                        rowindex2 = val.address
                        #print(rowindex2)
                        #print(rowindex1 + ':' + rowindex2)
                        print(rowindex1 + ':' + rowindex2)
                        work.range(rowindex1 + ':' + rowindex2).color = (226,239,218)
                    elif work.range(val.address).value == False:
                        rowindex1 = '$B' + str(val.row)
                        rowindex2 = val.address
                        print('FALSEEEEEEEEEEEE')
                        #print(val.address)
                        print(work.range(val.address).value)
                        print()  
                        work.range(rowindex1 + ':' + rowindex2).color = (255,153,153)
                        print(rowindex1 + ':' + rowindex2)
    else:
        print(work.name, "does not have a Passed Column")





#Code to test changing number formats for all numbers on one sheet
#Keep in mind the Z100 was chosen to make sure all relevant cells are grabbed
#May not be the best number depending on the number of applicants
#This works but slows code down significantly
'''
num_format_sheets = ['Corporate Non - Pro', 'Corporate Pro','Executives']
for she in num_format_sheets:
    work = final_wkbk.sheets[she]
    all_cells = work.range('C2:AD400')
    for cell in all_cells:
        cell_type = type(work.range(cell).value)
        cell_add = cell.address
        if cell_type == float:
            work.range(cell).number_format = '0.00'
            #print(cell_type)
            #print(cell_add)
        else:
            pass
'''

final_wkbk.sheets['Z Scores'].autofit('c')
final_wkbk.sheets['Z Scores']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Z Scores'].api.Tab.Color = 255

final_wkbk.sheets['Interview - Average'].autofit('c')
final_wkbk.sheets['Interview - Average']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Interview - Average'].api.Tab.Color = 255

final_wkbk.sheets['Executives'].autofit('c')
final_wkbk.sheets['Executives']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Executives'].api.Tab.Color = 16711680
   
final_wkbk.sheets['Summary'].autofit('c')
final_wkbk.sheets['Summary']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Summary'].api.Tab.Color =  16711680

final_wkbk.sheets['Custom'].autofit('c')
final_wkbk.sheets['Custom']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Custom'].api.Tab.Color = 16711680
                                          

final_wkbk.sheets['Multifamily'].autofit('c')
final_wkbk.sheets['Multifamily']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Multifamily'].api.Tab.Color = 16711680


final_wkbk.sheets['Corporate Misc'].autofit('c')
final_wkbk.sheets['Corporate Misc']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Corporate Misc'].api.Tab.Color = 16711680

final_wkbk.sheets['Corporate Pro'].autofit('c')
final_wkbk.sheets['Corporate Pro']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Corporate Pro'].api.Tab.Color = 16711680

final_wkbk.sheets['Corporate Non - Pro'].autofit('c')
final_wkbk.sheets['Corporate Non - Pro']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Corporate Non - Pro'].api.Tab.Color = 16711680

final_wkbk.sheets['Interview - Overall'].autofit('c')
final_wkbk.sheets['Interview - Overall']['C2'].expand('table').api.HorizontalAlignment = xw.constants.HAlign.xlHAlignCenter
final_wkbk.sheets['Interview - Overall'].api.Tab.Color = 255


final_wkbk.sheets['Download'].api.Tab.Color = 0


#Setting borders 
for work5 in final_wkbk.sheets:
    top_row5 = work5.range('B2').expand('right').value
    row5 = work5.range('H3:H150')
    if "Final Z-Score" not in top_row5:
        pass
    else:
        for i in row5:
            try:
                if i.value < 0:
                    #print(i.row)
                    row_num = i.row
                    row_index = 'A'+ str(row_num) + ':' + 'AF' + str(row_num)
                    work5.range(row_index).api.Borders.LineStyle = 1
                    work5.range(row_index).api.Borders.Weight = 3
                    break
            except:
                print("Issue with ", work5.name)


final_wkbk.save()
final_wkbk.close()
app.kill()
